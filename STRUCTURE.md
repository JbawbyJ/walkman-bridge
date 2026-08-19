# Project Structure

Architectural reference for Walkman Bridge. Pair this with `.cursorrules` (AI-facing) and `README.md` (setup-facing).

## Tree

```
walkman-bridge/
├── .cursorrules              # AI assistant context — keep in sync with this file
├── README.md                 # Setup, run, prerequisites
├── STRUCTURE.md              # This file — architecture and module responsibilities
├── START-WALKMAN-BRIDGE.bat  # Double-click launcher (non-technical users)
│
├── docs/
│   └── PROTOCOL.md           # Backend ↔ HeadlessCli JSON-lines protocol — the contract
│
├── scripts/
│   ├── setup.bat / setup.sh  # One-time environment setup
│   └── start.bat / start.sh  # Manual start equivalents
│
├── backend/
│   ├── main.py               # FastAPI app, route handlers, background task runner, track cache
│   ├── device.py             # Mount detection (Linux/macOS/Windows) + MOCK_DEVICE_PATH override
│   ├── transcode.py          # ffmpeg subprocess wrapper, audio normalization
│   ├── jsymphonic.py         # HeadlessCli subprocess wrapper — JSON-lines protocol, single-flight lock
│   ├── jobs.py               # In-memory job store, JobStatus enum, log_tail buffer
│   ├── requirements.txt
│   ├── requirements-dev.txt  # pytest etc.
│   ├── tests/                # pytest suite (fake-shim protocol tests, cache, lock, mock device)
│   └── vendor/
│       └── jsymphonic.jar    # Build from the companion fork (gitignored — see Licensing notes)
│
└── frontend/
    ├── index.html            # Vite entry, Google Fonts preload
    ├── package.json
    ├── postcss.config.js
    ├── tailwind.config.js    # Custom theme tokens — DO NOT add colors without updating this
    ├── vite.config.js        # /api → 127.0.0.1:8000 proxy
    └── src/
        ├── main.jsx          # React root
        ├── App.jsx           # Single-screen dashboard (device, dropzone, jobs, tracks)
        ├── api.js            # Thin fetch wrapper — all HTTP goes through here
        └── index.css         # Tailwind layers + CRT scanline overlay + custom keyframes
```

## Module responsibilities

### Backend

**`main.py`** — HTTP surface area only. Holds the FastAPI app, CORS config, Pydantic response schemas, and the `process_upload_job` background coroutine. No business logic; delegates to the four modules below. If you find yourself writing audio or device logic here, move it.

**`device.py`** — Knows how to find the Walkman. `find_walkman()` returns a `Path | None`; if `MOCK_DEVICE_PATH` is set (and has an `OMGAUDIO` dir) it wins — that's how the whole stack runs without hardware. `device_info(mount)` returns disk usage + track count. Cross-platform branching lives here and only here.

**`transcode.py`** — Owns ffmpeg. `normalize_to_mp3(src) -> Path` is the only public function. Raises `AudioError` on failure. Output is a temp MP3 the caller is responsible for cleaning up.

**`jsymphonic.py`** — Owns the JAR. The stock JSymphonic jar is GUI-only, so this module drives the `HeadlessCli` class our fork adds (`java -cp vendor/jsymphonic.jar org.naurd.media.jsymphonic.headless.HeadlessCli …`), speaking the JSON-lines protocol in `docs/PROTOCOL.md`. Exposes `device_details`, `list_tracks`, `add_tracks` (batched — one DB commit per upload job), `remove_track`. Every invocation is serialized behind a module-level lock: the JAR rewrites the device database and must never run twice concurrently. Raises `JSymphonicError` on failure. Java is resolved from `WALKMAN_BRIDGE_JAVA` or PATH.

**`jobs.py`** — In-memory state. `JobStore` is a singleton at module level. `Job` holds status, progress (0.0–1.0), message, and a 200-entry log deque. Thread-safe via `RLock` because `BackgroundTasks` may run off the main loop.

### Frontend

**`App.jsx`** — One component, intentionally. Three regions: device panel (left/top), dropzone + active job (right/main), track list (bottom). Two polling effects: device+tracks every 3s, active job every 1s. All state is `useState`; no context, no Redux, no Zustand.

**`api.js`** — `api.device()`, `api.tracks()`, `api.deleteTrack(id)`, `api.upload(files)`, `api.job(id)`. Every component goes through this. Do not call `fetch` directly from components.

**`index.css`** — Tailwind directives, the CRT scanline `body::before` overlay, `ring-pulse` (dropzone active), `pulse-dot` (device connected indicator), scrollbar styling. New animations go here.

**`tailwind.config.js`** — Theme tokens. New colors *must* be added here, not used as arbitrary values. Fonts are JetBrains Mono (`font-display`) and IBM Plex Sans (`font-body`).

## Data flow — a single upload

```
1. User drops MP3 → onDrop(e) in App.jsx
2. api.upload(files) → POST /api/upload (multipart)
3. main.py:
   a. find_walkman() — fail fast if no device
   b. Save uploads to /tmp/walkman-bridge/<job_id>/
   c. job_store.create(job_id, total_files=N)
   d. background_tasks.add_task(process_upload_job, ...)
   e. Return {job_id} immediately
4. App.jsx starts polling /api/jobs/{job_id} every 1s
5. process_upload_job:
   a. transcode.normalize_to_mp3(src) per file → ffmpeg subprocess
   b. jsymphonic.add_tracks(mount, all_mp3s, on_event) → ONE java subprocess,
      one database commit; protocol events stream into job.log/progress
6. On done/failure, status flips; track cache invalidated; frontend stops polling

Note: `/api/tracks` serves an in-process cache (refreshed after mutations or with
`?refresh=1`) — the dashboard's 3s polling must never spawn a JVM.
```

## Where new things go

| If you're adding…                          | Put it in…                                       |
|--------------------------------------------|--------------------------------------------------|
| A new HTTP endpoint                        | `main.py`                                        |
| Audio format support / encoding tweaks     | `transcode.py`                                   |
| New JSymphonic command (playlist, sync)    | `jsymphonic.py`                                  |
| Device-specific behavior (new model)       | `device.py`                                      |
| Job persistence (SQLite, etc.)             | `jobs.py` — replace `JobStore` impl, keep API    |
| A new visual region                        | New component imported into `App.jsx`            |
| Cross-cutting state (theme, settings)      | `useState` in `App.jsx` + prop drill; no Context |
| A new color/font/animation                 | `tailwind.config.js` and/or `index.css` first    |
| Tests                                      | `backend/tests/` (doesn't exist yet — create it) |

## Boundaries to respect

- **`main.py` never calls subprocesses directly.** Always through `transcode` or `jsymphonic`.
- **`App.jsx` never calls `fetch` directly.** Always through `api.js`.
- **`device.py` never imports from `jsymphonic.py` or `transcode.py`.** It only knows about filesystems.
- **`jobs.py` never imports from anything else in the project.** It's a leaf module.

If a refactor would violate one of these, surface that as a tradeoff before doing it.

## Licensing notes

- JSymphonic is GPL-3.0 — the JAR lives in `backend/vendor/` for local use only and is gitignored. Users build it from the companion fork repo (which carries the `HeadlessCli` addition and Windows fixes, GPL like the rest of JSymphonic) per the README.
- ffmpeg is LGPL/GPL depending on build. Same logic — call it as an external process, do not bundle.
- This project's own code can be MIT or whatever you choose, but the runtime composition is GPL-touching, so be deliberate before distributing binaries.
