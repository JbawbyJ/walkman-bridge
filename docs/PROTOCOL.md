# Walkman Bridge ↔ HeadlessCli protocol

The backend never touches the JSymphonic GUI. It invokes the headless shim that lives in our
JSymphonic fork and speaks **JSON-lines over stdout**: one JSON object per line, `event` field
discriminates. All human-readable/log noise goes to **stderr**. Exit code `0` = success,
nonzero = failure (a `fatal` event precedes a nonzero exit whenever possible).

## Invocation

```
<java> -cp <jsymphonic-fat-jar> org.naurd.media.jsymphonic.headless.HeadlessCli <command> [args]
```

- `<java>`: resolved by the backend — env `WALKMAN_BRIDGE_JAVA` if set, else `java` on PATH.
  The launcher scripts point it at the portable JDK in `tools/`.
- The jar is the normal `jsymphonic-*-jar-with-dependencies.jar` built from our fork
  (`mvn package`); the GUI remains the jar's default main class — the shim is invoked by
  explicit classname, so the same jar serves both technical (GUI) and non-technical
  (dashboard) users.

## Commands

### `info --device <mount>`
Validates the mount and reports device state. Events:

```json
{"event":"device","ok":true,"generation":7,"trackCount":42,"freeBytes":123,"totalBytes":456}
```

### `list --device <mount>`
One event per track on the device, then a terminator:

```json
{"event":"track","id":"3","title":"...","artist":"...","album":"...","durationSeconds":241,"format":"MP3","sizeBytes":123}
{"event":"listEnd","count":42}
```

`id` is opaque to the backend (stable within a single device DB state; it is re-fetched
after every mutation, never persisted).

### `add --device <mount> <file> [<file> ...]`
Imports the given audio files (already-normalized MP3s) and commits the DB in **one**
`applyChanges()` cycle. Events, in order:

```json
{"event":"scan","files":0}
{"event":"start","files":3}
{"event":"plan","export":0,"delete":0,"decode":0,"encode":0,"transfer":3,"database":17}
{"event":"step","step":"transfer|decode|encode|delete|update","state":"started|finished","error":null}
{"event":"file","step":"transfer","name":"Artist - Title"}
{"event":"progress","step":"transfer","percent":42.5,"speedKBps":900.1}
{"event":"done"}
```

Notes the consumer must honor:

- `scan` events also fire during device load (before `start`) — informational.
- `percent` is **per file**, not per batch; `file`/`progress` events are also
  emitted for the database-update step (`step":"update"`) — only
  `step":"transfer"` progress should drive a batch progress bar.
- **Partial failure**: the legacy engine finishes the DB write even when some
  operations fail, reporting them via step `error` strings. The shim collects
  those and, if any occurred, emits `fatal` ("Completed with errors: ...") and
  exits 1 **instead of** `done`. Exit 0 + `done` therefore means full success.

### `del --device <mount> --id <id>`
Schedules deletion of the track whose id matches a fresh `list` enumeration, commits.
Stream shape: `scan`, `start`, `plan`, a `delete` step (with `file`/`progress`
events), then the `update` step rebuilding the 17 DB files, then:

```json
{"event":"done"}
```

The same partial-failure rule as `add` applies: a failed deletion surfaces as
`fatal` + exit 1, never as a silent `done`.

### Errors

```json
{"event":"fatal","message":"human-readable reason"}
```

followed by exit code 1. Unknown/missing/malformed args (including non-numeric
`--generation`/`--idle-timeout` values) → usage on stderr, exit 2.

## Backend contract (`backend/jsymphonic.py`)

- **Single-flight:** every shim invocation is serialized behind one module-level lock —
  the JAR rewrites the device DB and must never run concurrently.
- **Batch:** one `add` invocation per upload job (all files), never per-file.
- `list_tracks(mount) -> list[dict]`, `add_tracks(mount, files, on_event) -> None`,
  `remove_track(mount, track_id) -> None`, `device_details(mount) -> dict`.
- `on_event` receives each parsed JSON object; the FastAPI job maps `progress`/`file`
  events into the job store so the dashboard's live log stays truthful.
- Unparseable stdout lines are logged and skipped (forward compatibility).
- Timeouts: `info`/`list` 120 s, `del` 600 s (a delete rebuilds the whole DB,
  like add), `add` 3600 s. On timeout: kill process, raise. A timer that fires
  as a successful run exits is not a timeout — the exit code decides.
- Nonzero exit → `JSymphonicError(message from last fatal event, else stderr tail)`.

## Mock device

`MOCK_DEVICE_PATH` env var (backend): when set, `device.py` reports that path as the
connected Walkman instead of scanning drives. A mock device is any folder containing an
empty `OMGAUDIO/` directory — a valid starting state; JSymphonic regenerates the full DB
from nothing. Real-hardware behavior remains unverified until Milestone 4 (see
BUILD-READINESS.md §5 — S70x history requires a full device backup before first write).
