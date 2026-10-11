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
{"event":"fatal","message":"human-readable reason","code":"PLAYLIST_REF_MISSING"}
```

`code` is optional. The only values the backend recognizes are:

- `PLAYLIST_REF_MISSING` — a dangling track ID blocks playlist writes
- `PLAYLIST_JOURNAL_PENDING` — a leftover `.jsymphonic-playlist-transaction` journal blocks reads and writes
- `PLAYLIST_SLOTS_EXHAUSTED` — all 2048 lifetime playlist slots are used
- `PLAYLIST_LIBRARY_NOT_LOADED` — playlist repair refused because the library is not loaded. Nothing was written
- `DEVICE_FILE_LOCKED` — a device file is locked. The previous snapshot is still in place
- `DEVICE_ROLLBACK_FAILED` — rollback failed. The device state is uncertain

Any other fatal omits `code`. A missing code is generic. The backend never
derives a code by parsing `message`. An unrecognized string, or a non-string
`code`, is dropped to generic and does not raise. Those six names are
`FatalCode` in `backend/jsymphonic.py`.

`DEVICE_FILE_LOCKED` may also carry a top-level `path` on the `fatal` event.
When `path` is a string, HTTP errors copy it to `detail.fatal_path` and device
job files copy it to `files[].fatal_path`. A missing or non-string `path` omits
that field. Nested objects and message text are not a path. The exact string
format (separators, relative versus absolute) is pending confirmation from
Builder B. The backend copies the string unchanged.

Followed by exit code 1. Unknown/missing/malformed args (including non-numeric
`--generation`/`--idle-timeout` values) → usage on stderr, exit 2.

The fatal `code` is only the top-level field of the `fatal` event. A `code`
nested under another object, or the same words inside `message`, is not a code.

### Playlist recovery

Confirmed against jsymphonic #4 @ `5763643`. Argv matches the other commands
(`--device` immediately after the command):

- `playlist-recover --device <mount> --inspect` — read-only. HTTP `GET /api/device/playlist-recovery/inspect`.
- `playlist-recover --device <mount>` — mutating. HTTP `POST /api/device/playlist-recovery/recover`.
- `playlist-repair --device <mount>` — mutating. HTTP `POST /api/device/playlist-recovery/repair`.

Inspect, recover, and repair each take the shim lock. Success is a final
`{"event":"done"}`. Exit 0 without that marker is a failure. Recover and repair
then set `needs_reconcile`. Inspect does not. A `fatal` event is a refusal and
replaces `done`.

`state` and `outcome` are read only from the `playlistJournal` event. Inspect
does not emit `outcome`. The backend does not parse `message`, and it does not
read those fields from `done` or from any other event. A missing field, or a
value outside the list, is JSON `null`.

Inspect `state`:

- `committed` — the journal has a commit marker
- `uncommitted` — a journal is present and has no commit marker
- `none` — no journal

Recover `outcome` on that same `playlistJournal` event:

- `rolled_forward` — the journal had a commit marker and that save was finished
- `discarded` — no commit marker; the journal is removed, the interrupted change is lost, and the device keeps its pre-change playlist state
- `none` — no journal

```json
{"event":"playlistJournal","state":"committed","outcome":"rolled_forward","files":["OMGAUDIO/10F00/1000.mp3"]}
{"event":"done"}
```

`covers` comes from `playlistJournal.files`. When `files` is a list of strings,
the HTTP body is `covers.files`, those strings copied unchanged. They are paths
relative to the device. If `files` is missing, or any value is not a string,
`covers` is null. `playlistIds` and `trackIds` are not coverage. The exact path
format is pending confirmation from Builder B.

Repair success emits the summary first, then one playlist row per playlist, then `done`:

```json
{"event":"playlistRepair","prunedCount":2,"prunedTrackIds":[3],"playlistIds":[1]}
{"event":"playlist","id":"1","name":"Keep","trackIds":["1"]}
{"event":"done"}
```

`prunedTrackIds` and `playlistIds` on `playlistRepair` are JSON numbers. The API
returns them as decimal strings so they match the rest of the API. Playlist row
`id` and `trackIds` are already strings and do not replace the summary.
`prunedCount` counts removed member references, so it can be larger than
`len(prunedTrackIds)`. It is not checked against that length. Zero is a real
success:

```json
{"event":"playlistRepair","prunedCount":0,"prunedTrackIds":[],"playlistIds":[]}
{"event":"done"}
```

An empty or unloaded library writes nothing. Repair checks the cached device
track list first (listing tracks when the cache has no entry for this volume).
An empty list is HTTP 409 `detail.code` `library_not_loaded` and
`detail.fatal_code` `PLAYLIST_LIBRARY_NOT_LOADED`, before `playlist-repair` runs.
If the engine still refuses, it emits `fatal` with that same code (a scan line
may precede it) and the HTTP result uses the same `detail.code` and
`fatal_code`. Neither path sets `needs_reconcile`.

```json
{"event":"fatal","message":"Playlist repair refused: the library is not loaded","code":"PLAYLIST_LIBRARY_NOT_LOADED"}
```

`DEVICE_FILE_LOCKED` also leaves the previous snapshot in place, so
`needs_reconcile` stays false on every device write, the same as a pre-write
refusal. `DEVICE_ROLLBACK_FAILED` keeps `needs_reconcile` true.

Timeouts: inspect 120 s, recover and repair 600 s. On timeout the shim is
killed. A recover or repair timeout sets `needs_reconcile`. An inspect timeout
does not. The device track cache for recover and repair is cleared in the
request `finally`, including when the request fails. Inspect does not clear it.

HTTP:

| Action id | Fatal code | Call |
| --- | --- | --- |
| `inspect_recover` | `PLAYLIST_JOURNAL_PENDING`, `DEVICE_ROLLBACK_FAILED` | `GET` inspect, then `POST` recover |
| `repair` | `PLAYLIST_REF_MISSING` | `POST /api/device/playlist-recovery/repair` |
| `free_slots` | `PLAYLIST_SLOTS_EXHAUSTED` | existing `DELETE /api/device/playlists/{id}` (`playlist-delete`). No new endpoint. |
| `reconnect_retry` | `PLAYLIST_LIBRARY_NOT_LOADED` | no endpoint. The owner reconnects the Walkman, then `repairPlaylists` runs again. |
| `close_and_retry` | `DEVICE_FILE_LOCKED` | no endpoint. The UI shows `fatal_path` and the owner retries the original action. |
| `GENERIC` | null | no recovery action |

`frontend/src/api.js` exports the same map as `RECOVERY_ACTIONS` and the helpers `inspectPlaylistJournal`, `recoverPlaylistJournal`, and `repairPlaylists`. `free_slots` uses the existing `deleteDevicePlaylist`. `reconnect_retry` and `close_and_retry` add no route.

Inspect success:

```json
{"exists":true,"state":"committed","covers":{"files":["OMGAUDIO/10F00/1000.mp3"]}}
```

`exists` is false when `state` is `none`, and null when `state` is null. Inspect errors match other playlist reads: `{message, fatal_code}` when the fatal `code` is known, plus `fatal_path` when that value is a string. Otherwise the detail is a string.

Recover success: `{"ok":true,"outcome":"discarded","job_id":"..."}`. `outcome` null means the `playlistJournal` event did not emit a known value.

Repair success: `{"ok":true,"job_id":"...","pruned_count":2,"pruned_track_ids":["3"],"playlist_ids":["1"]}`. `pruned_count` null means the count field was missing or not a non-negative integer. A missing or invalid summary id list is null.

Recover and repair failures use the write error shape: `detail.code` (`verify_device_state`, `playlist_failed`, or `library_not_loaded`) and `detail.fatal_code`. `library_not_loaded` is only the empty-library refusal. `DEVICE_FILE_LOCKED` uses `playlist_failed` because nothing was written, and includes `detail.fatal_path` when `path` is a string. `DEVICE_ROLLBACK_FAILED` uses `verify_device_state`.

## Backend contract (`backend/jsymphonic.py`)

- **Single-flight:** every shim invocation is serialized behind one module-level lock —
  the JAR rewrites the device DB and must never run concurrently.
- **Batch:** one `add` invocation per upload job (all files), never per-file.
- `list_tracks(mount) -> list[dict]`, `add_tracks(mount, files, on_event) -> None`,
  `remove_track(mount, track_id) -> None`, `device_details(mount) -> dict`.
- `on_event` receives each parsed JSON object; the FastAPI job maps `progress`/`file`
  events into the job store so the dashboard's live log stays truthful.
- Unparseable stdout lines are logged and skipped (forward compatibility).
- Timeouts: `info`/`list`/`playlist-recover --inspect` 120 s, `del` and playlist
  recover/repair 600 s (a delete rebuilds the whole DB,
  like add), `add` 3600 s. On timeout: kill process, raise. A timer that fires
  as a successful run exits is not a timeout — the exit code decides.
- Nonzero exit → `JSymphonicError(message from last fatal event, else stderr tail)`.
  A recognized fatal `code` is stored on that exception. Frontend HTTP errors
  keep their application `detail.code` (`playlist_failed`, `verify_device_state`,
  `library_not_loaded`, `delete_failed`) and add `fatal_code` (`null` when generic).
  A string fatal `path` is copied to `fatal_path`. Playlist and
  track reads that carry a known code return `{message, fatal_code}` instead of
  a string detail. Failed device job files include `fatal_code` the same way.
  `frontend/src/api.js` copies `detail.fatal_code` and a string `detail.fatal_path`
  onto the thrown error.

## Mock device

`MOCK_DEVICE_PATH` env var (backend): when set, `device.py` reports that path as the
connected Walkman instead of scanning drives. A mock device is any folder containing an
empty `OMGAUDIO/` directory — a valid starting state; JSymphonic regenerates the full DB
from nothing. Real-hardware behavior remains unverified until Milestone 4 (see
BUILD-READINESS.md §5 — S70x history requires a full device backup before first write).
