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
        files = args[3:]
        emit({"event": "start", "files": len(files)})
        emit({"event": "step", "step": "transfer", "state": "started", "error": None})
        for path in files:
            emit({"event": "file", "step": "transfer", "name": path})
            emit({"event": "progress", "step": "transfer", "percent": 50.0, "speedKBps": 900.1})
            emit({"event": "progress", "step": "transfer", "percent": 100.0, "speedKBps": 900.1})
        emit({"event": "step", "step": "transfer", "state": "finished", "error": None})
        emit({"event": "done"})
        return 0

    if scenario == "happy_del":
        emit({"event": "done"})
        return 0

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

    print(f"unknown scenario {scenario}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
