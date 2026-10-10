"""Isolated JSONL entry point for link imports. Never import from user paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from link_import import LinkImportError, _download_link_inprocess


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class ProgressEmitter:
    MAX_EVENTS = 101

    def __init__(self) -> None:
        self.last_percent = -1
        self.last_unknown_bucket = -1
        self.last_status = None
        self.events = 0

    def __call__(self, status: dict) -> None:
        state = str(status.get("status") or "downloading")[:32]
        downloaded = max(0, min(int(status.get("downloaded_bytes") or 0), 1 << 40))
        total = max(0, min(int(status.get("total_bytes") or status.get("total_bytes_estimate") or 0), 1 << 40))
        is_terminal = state in {"finished", "error"}
        if self.events >= self.MAX_EVENTS or (self.events >= self.MAX_EVENTS - 1 and not is_terminal):
            return
        if total:
            percent = min(100, int(downloaded * 100 / total))
            if percent <= self.last_percent and state == self.last_status:
                return
            self.last_percent = percent
        else:
            bucket = downloaded // (5 * 1024 * 1024)
            if bucket <= self.last_unknown_bucket and state == self.last_status:
                return
            self.last_unknown_bucket = bucket
            percent = 0
        self.last_status = state
        self.events += 1
        emit({
            "type": "progress",
            "value": {
                "status": state,
                "percent": percent,
                "downloaded_bytes": downloaded,
                "total_bytes": total,
            },
        })


def main() -> int:
    line = sys.stdin.buffer.readline(32769)
    if not line or len(line) > 32768:
        return 2
    try:
        request = json.loads(line)
        media = _download_link_inprocess(
            request["url"], Path(request["directory"]), int(request["max_bytes"]), progress=ProgressEmitter()
        )
        emit({"type": "result", "path": str(media.path), "name": media.name, "metadata": media.metadata})
        return 0
    except (LinkImportError, KeyError, TypeError, ValueError):
        emit({"type": "error", "message": "The isolated worker could not import this public track."})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
