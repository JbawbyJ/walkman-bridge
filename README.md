# Walkman Bridge

A local web dashboard for putting music on a **Sony NW-S705F Walkman** (2007) from a modern computer — no SonicStage, no Linux, no command line.

Drop audio files onto the page; they're converted to Walkman-friendly MP3 and written into the device's proprietary OMGAUDIO database. Runs entirely on your machine: the browser talks to a small local server on `127.0.0.1`, which uses `ffmpeg` for conversion and a headless build of [JSymphonic](https://github.com/georgewoodall82/jsymphonic) for the device database.

```
Browser (React dashboard)
   ↓ HTTP (localhost only)
FastAPI server (127.0.0.1:8000)
   ↓ subprocess
   ├── ffmpeg               → convert anything → MP3 192k/44.1kHz
   └── jsymphonic.jar       → HeadlessCli: OMA-wrap + rebuild OMGAUDIO DB
   ↓ filesystem
Walkman (USB mass storage drive)
```

## For non-technical users

1. Run `scripts\setup.bat` once (double-click). It prepares everything and tells you if anything is missing.
2. Plug in the Walkman with its WM-PORT cable.
3. Double-click **`START-WALKMAN-BRIDGE.bat`**. Your browser opens the dashboard.
4. Drag songs in. Watch them transfer. Done.

To stop, close the black console window (or press `Ctrl+C` in it).

## Prerequisites

| What | Why | Check |
|---|---|---|
| Python 3.10+ | runs the server | `python --version` |
| Node.js 18+ | builds the dashboard (once) | `node --version` |
| ffmpeg | audio conversion | `ffmpeg -version` |
| Java 17/21 JRE | runs the JSymphonic engine | auto-detected: `WALKMAN_BRIDGE_JAVA` env var, a portable JDK in `..\tools\jdk*`, or `java` on PATH |
| `jsymphonic.jar` | the transfer engine | place at `backend\vendor\jsymphonic.jar` |

**About the jar:** build it from [the companion JSymphonic fork](https://github.com/JbawbyJ/jsymphonic) with `mvn package` (use the `jar-with-dependencies` artifact). The fork adds the headless CLI this app drives, plus Windows file-locking fixes. JSymphonic is GPL-3.0, so the jar is not bundled in this repo — you build or download it yourself.

## Manual start (developers)

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # POSIX: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000

# Frontend — dev server with hot reload (proxies /api to :8000)
cd frontend
npm install
npm run dev
```

In production mode there is no second process: `npm run build` once, and the FastAPI server serves `frontend/dist` itself at `http://127.0.0.1:8000`.

### Developing without a Walkman

Set `MOCK_DEVICE_PATH` to any folder containing an empty `OMGAUDIO` directory and the app treats it as the connected device — JSymphonic regenerates the entire database from scratch, so transfers, listing, and deletion are all fully exercisable against a plain folder:

```bash
mkdir -p /tmp/mockdev/OMGAUDIO
MOCK_DEVICE_PATH=/tmp/mockdev uvicorn main:app
```

## Before your first real transfer — read this

The NW-S705F's database format was reverse-engineered decades ago, and JSymphonic's historical track record on the S70x series specifically is thin. This app is verified against mock devices; **real-hardware behavior is verified only by you, on your device.**

1. **Back up the device first.** Copy the entire Walkman drive (including `OMGAUDIO`) to a folder on your PC. Restoring that copy restores the device.
2. **One-way door:** SonicStage cannot manage a JSymphonic-written database. If you still use SonicStage, switching back later means wiping the device.
3. Start with one track, verify it plays on the device, then trust it with more.

## Security model

Single user, local machine. The server binds to `127.0.0.1` only — nothing is reachable from the network. No accounts, no auth, no telemetry. Uploaded files are processed in a temp folder and deleted after transfer.

## Repository layout

See [STRUCTURE.md](STRUCTURE.md) for the module map and [docs/PROTOCOL.md](docs/PROTOCOL.md) for the backend ↔ HeadlessCli JSON-lines protocol.

## License

The Walkman Bridge application code is MIT. The JSymphonic engine (separate repo, separate artifact, invoked as a subprocess) is GPL-3.0; its jar is intentionally not distributed with this repository.
