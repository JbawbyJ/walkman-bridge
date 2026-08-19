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
{"event":"start","files":3}
{"event":"step","step":"transfer|decode|encode|delete|update","state":"started|finished","error":null}
{"event":"file","step":"transfer","name":"song.mp3"}
{"event":"progress","step":"transfer","percent":42.5,"speedKBps":900.1}
{"event":"done"}
```

### `del --device <mount> --id <id>`
Schedules deletion of the track whose id matches a fresh `list` enumeration, commits, then:

```json
{"event":"done"}
```

### Errors

```json
{"event":"fatal","message":"human-readable reason"}
```

followed by exit code 1. Unknown/missing args → usage on stderr, exit 2.

## Backend contract (`backend/jsymphonic.py`)

- **Single-flight:** every shim invocation is serialized behind one module-level lock —
  the JAR rewrites the device DB and must never run concurrently.
- **Batch:** one `add` invocation per upload job (all files), never per-file.
- `list_tracks(mount) -> list[dict]`, `add_tracks(mount, files, on_event) -> None`,
  `remove_track(mount, track_id) -> None`, `device_details(mount) -> dict`.
- `on_event` receives each parsed JSON object; the FastAPI job maps `progress`/`file`
  events into the job store so the dashboard's live log stays truthful.
- Unparseable stdout lines are logged and skipped (forward compatibility).
- Timeouts: `info`/`list`/`del` 120 s, `add` 3600 s. On timeout: kill process, raise.
- Nonzero exit → `JSymphonicError(message from last fatal event, else stderr tail)`.

## Mock device

`MOCK_DEVICE_PATH` env var (backend): when set, `device.py` reports that path as the
connected Walkman instead of scanning drives. A mock device is any folder containing an
empty `OMGAUDIO/` directory — a valid starting state; JSymphonic regenerates the full DB
from nothing. Real-hardware behavior remains unverified until Milestone 4 (see
BUILD-READINESS.md §5 — S70x history requires a full device backup before first write).
