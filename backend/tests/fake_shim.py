"""
Fake HeadlessCli shim for the tests.

The tests point jsymphonic.SHIM_CMD_PREFIX at
[sys.executable, this file, <scenario>], so the real shim args land in argv
after the scenario name. Emits scripted JSON-lines per docs/PROTOCOL.md.
Every print flushes — the wrapper's streaming behavior is what's under test.
"""
from __future__ import annotations

import json
import sys
import time


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def emit_line(line: str) -> None:
    """Print one confirmed HeadlessCli line with its bytes unchanged."""
    print(line, flush=True)


# jsymphonic #4 @ ba26514. Compact JSON, key order included. Do not reformat.
LOCKED_FATAL_LINE = '{"event":"fatal","message":"Device file is locked","code":"DEVICE_FILE_LOCKED","path":"OMGAUDIO/10F00/10000001.OMA"}'
READ_ONLY_FATAL_LINE = '{"event":"fatal","message":"Device file is read-only","code":"DEVICE_FILE_READ_ONLY","path":"OMGAUDIO/10F00/10000001.OMA"}'
# jsymphonic #4 @ 9d96537. playlist-create, playlist-update, playlist-delete, and playlist-repair emit this too.
ROLLBACK_FATAL_LINE = '{"event":"fatal","message":"Database update failed; incomplete recovery requires a verified backup","code":"DEVICE_ROLLBACK_FAILED"}'
# jsymphonic #4 @ 979b355. Compact JSON, key order included. Do not reformat.
PROBE_RESTORE_FATAL_LINE = '{"event":"fatal","message":"Device file probe could not be restored","code":"DEVICE_PROBE_RESTORE_FAILED","path":"OMGAUDIO/10F00/10000001.OMA","probe_path":"OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe"}'
PROBE_CONFLICT_FATAL_LINE = '{"event":"fatal","message":"Device file probe conflicts with the track","code":"DEVICE_PROBE_CONFLICT","path":"OMGAUDIO/10F00/10000001.OMA","probe_path":"OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe"}'
PROBE_PENDING_WARNING_LINE = '{"event":"warning","code":"DEVICE_PROBE_PENDING","path":"OMGAUDIO/10F00/10000001.OMA","probe_path":"OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe"}'


def main() -> int:
    scenario = sys.argv[1]
    args = sys.argv[2:]

    if scenario == "happy_info":
        emit({"event": "device", "ok": True, "generation": 7, "trackCount": 2,
              "freeBytes": 111, "totalBytes": 222})
        return 0

    if scenario == "happy_list":
        emit({"event": "track", "id": "1", "title": "Alpha", "artist": "A",
              "album": "AA", "durationSeconds": 100, "format": "MP3", "sizeBytes": 1})
        emit({"event": "track", "id": "2", "title": "Beta", "artist": "B",
              "album": "BB", "durationSeconds": 200, "format": "MP3", "sizeBytes": 2})
        emit({"event": "listEnd", "count": 2})
        return 0

    if scenario == "happy_add":
        # args: add --device <mount> <file> [<file> ...]
        # Mirrors the REAL shim's stream: scan + plan first, per-file transfer
        # progress, then the database-update step with its own file/progress
        # events (which must NOT move the job's progress bar).
        files = args[3:]
        emit({"event": "scan", "files": 0})
        emit({"event": "start", "files": len(files)})
        emit({"event": "plan", "export": 0, "delete": 0, "decode": 0,
              "encode": 0, "transfer": len(files), "database": 17})
        emit({"event": "step", "step": "transfer", "state": "started", "error": None})
        for path in files:
            emit({"event": "file", "step": "transfer", "name": path})
            emit({"event": "progress", "step": "transfer", "percent": 50.0, "speedKBps": 900.1})
            emit({"event": "progress", "step": "transfer", "percent": 100.0, "speedKBps": 900.1})
        emit({"event": "step", "step": "transfer", "state": "finished", "error": None})
        emit({"event": "step", "step": "update", "state": "started", "error": None})
        for name in ("00GTRLST", "04CNTINF"):
            emit({"event": "file", "step": "update", "name": name})
            emit({"event": "progress", "step": "update", "percent": 100.0, "speedKBps": 0.0})
        emit({"event": "step", "step": "update", "state": "finished", "error": None})
        emit({"event": "done"})
        return 0

    if scenario == "add_partial_errors":
        # The real shim's partial-failure shape: the DB is written, but a step
        # reported an error -> fatal summary + exit 1 (never a bare done).
        files = args[3:]
        emit({"event": "start", "files": len(files)})
        emit({"event": "step", "step": "transfer", "state": "started", "error": None})
        emit({"event": "file", "step": "transfer", "name": files[0] if files else "x"})
        emit({"event": "step", "step": "transfer", "state": "finished",
              "error": "1 file could not be copied"})
        emit({"event": "step", "step": "update", "state": "finished", "error": None})
        emit({"event": "fatal",
              "message": "Completed with errors: transfer: 1 file could not be copied"})
        return 1

    if scenario == "happy_del":
        # Real del stream: delete step, then the full DB update step.
        emit({"event": "scan", "files": 1})
        emit({"event": "start", "files": 1})
        emit({"event": "plan", "export": 0, "delete": 1, "decode": 0,
              "encode": 0, "transfer": 0, "database": 17})
        emit({"event": "step", "step": "delete", "state": "started", "error": None})
        emit({"event": "file", "step": "delete", "name": "Some - Track"})
        emit({"event": "step", "step": "delete", "state": "finished", "error": None})
        emit({"event": "step", "step": "update", "state": "started", "error": None})
        emit({"event": "step", "step": "update", "state": "finished", "error": None})
        emit({"event": "done"})
        return 0

    if scenario == "del_failed":
        # Deletion failed (e.g. file still in use): fatal + exit 1.
        emit({"event": "start", "files": 1})
        emit({"event": "step", "step": "delete", "state": "finished",
              "error": "1 file could not be deleted"})
        emit({"event": "fatal",
              "message": "Completed with errors: delete: 1 file could not be deleted"})
        return 1

    if scenario == "fatal":
        emit({"event": "start", "files": 1})
        emit({"event": "fatal", "message": "device database is corrupt"})
        return 1

    if scenario == "garbage":
        print("JSymphonic v0.5.4 booting", flush=True)     # not JSON
        emit({"event": "track", "id": "1", "title": "Alpha", "artist": "A",
              "album": "AA", "durationSeconds": 100})
        print("[]", flush=True)                            # JSON but not an object
        print("{broken json", flush=True)
        emit({"event": "listEnd", "count": 1})
        return 0

    if scenario == "hang":
        emit({"event": "start", "files": 1})
        time.sleep(30)                                     # wrapper must kill us first
        return 0

    if scenario == "stderr_fail":
        print("Exception in thread main: boom", file=sys.stderr)
        print("  at HeadlessCli.main", file=sys.stderr)
        return 3

    if scenario == "stamp":
        # For the single-flight test: two overlapping runs would interleave stamps.
        emit({"event": "stamp", "t": time.time()})
        time.sleep(0.4)
        emit({"event": "stamp", "t": time.time()})
        return 0

    if scenario == "echo_args":
        emit({"event": "args", "argv": args})
        return 0

    if scenario == "playlist_inspect_committed":
        # files on playlistJournal are coverage. Ids and the done line are not.
        emit({"event": "playlistJournal", "state": "committed",
              "files": ["01TREE22.DAT", "10F00/10000001.OMA", "tree", "info"],
              "playlistIds": ["4"], "trackIds": ["11", "1"], "message": "uncommitted discarded"})
        emit({"event": "done", "state": "uncommitted", "outcome": "discarded",
              "files": ["ignored.mp3"]})
        return 0

    if scenario == "playlist_inspect_uncommitted":
        emit({"event": "playlistJournal", "state": "uncommitted", "message": "committed",
              "files": "OMGAUDIO/10F00/1000.mp3"})
        emit({"event": "done", "state": "none", "files": ["ignored.mp3"]})
        return 0

    if scenario == "playlist_inspect_none":
        emit({"event": "playlistJournal", "state": "none", "message": "committed journal",
              "files": []})
        emit({"event": "done", "state": "committed"})
        return 0

    if scenario == "playlist_inspect_missing":
        # Success with no playlistJournal. Message text is not a state.
        emit({"event": "done", "message": "state committed uncommitted none",
              "files": ["OMGAUDIO/10F00/1000.mp3"], "state": "committed"})
        return 0

    if scenario == "playlist_inspect_unknown":
        emit({"event": "step", "state": "finished", "message": "committed"})
        emit({"event": "playlistJournal", "state": "finished", "message": "committed",
              "files": [1]})
        emit({"event": "done", "state": "committed", "files": ["OMGAUDIO/10F00/1000.mp3"]})
        return 0

    if scenario == "playlist_recover_rolled_forward":
        emit({"event": "playlistJournal", "state": "committed", "outcome": "rolled_forward",
              "message": "discarded"})
        emit({"event": "done", "outcome": "discarded", "state": "none"})
        return 0

    if scenario == "playlist_recover_discarded":
        emit({"event": "playlistJournal", "state": "uncommitted", "outcome": "discarded",
              "message": "rolled_forward"})
        emit({"event": "done", "outcome": "rolled_forward"})
        return 0

    if scenario == "playlist_recover_none":
        emit({"event": "playlistJournal", "outcome": "none", "message": "rolled_forward"})
        emit({"event": "done", "outcome": "discarded"})
        return 0

    if scenario == "playlist_recover_missing":
        emit({"event": "done", "message": "outcome rolled_forward discarded none",
              "outcome": "rolled_forward"})
        return 0

    if scenario == "playlist_recover_unknown":
        emit({"event": "playlistJournal", "outcome": "committed", "message": "rolled_forward"})
        emit({"event": "done", "outcome": "rolled_forward"})
        return 0

    if scenario == "playlist_repair_zero":
        emit({"event": "playlistRepair", "prunedCount": 0, "prunedTrackIds": [], "playlistIds": [],
              "message": "refused because of an empty mount"})
        emit({"event": "done"})
        return 0

    if scenario == "playlist_repair_some":
        # prunedCount counts member references and can exceed the id list.
        # Summary ids are numbers. The following playlist row uses strings.
        emit({"event": "playlistRepair", "prunedCount": 2, "prunedTrackIds": [3], "playlistIds": [1]})
        emit({"event": "playlist", "id": "1", "name": "Keep", "trackIds": ["1"]})
        emit({"event": "done", "prunedTrackIds": [9], "playlistIds": [4]})
        return 0

    if scenario == "playlist_repair_missing":
        emit({"event": "done", "message": "pruned zero tracks", "prunedCount": 0,
              "prunedTrackIds": [3], "playlistIds": [1]})
        return 0

    if scenario == "playlist_repair_empty_mount":
        emit({"event": "scan", "message": "looking at the mount"})
        emit({"event": "fatal", "message": "Playlist repair refused: library is not loaded",
              "code": "PLAYLIST_LIBRARY_NOT_LOADED"})
        return 1

    if scenario == "playlist_file_locked":
        emit_line(LOCKED_FATAL_LINE)
        return 1

    if scenario == "playlist_file_read_only":
        emit_line(READ_ONLY_FATAL_LINE)
        return 1

    if scenario == "playlist_rollback_failed":
        emit_line(ROLLBACK_FATAL_LINE)
        return 1

    if scenario == "playlist_probe_restore_failed":
        emit_line(PROBE_RESTORE_FATAL_LINE)
        return 1

    if scenario == "playlist_probe_conflict":
        emit_line(PROBE_CONFLICT_FATAL_LINE)
        return 1

    if scenario == "device_probe_pending":
        emit_line(PROBE_PENDING_WARNING_LINE)
        return 0

    if scenario == "playlist_fatal_nested_code":
        emit({"event": "fatal", "message": "PLAYLIST_REF_MISSING",
              "details": {"code": "PLAYLIST_JOURNAL_PENDING"}})
        return 1

    print(f"unknown scenario {scenario}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
