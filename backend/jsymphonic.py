"""
JSymphonic headless shim wrapper.

Speaks the JSON-lines protocol in docs/PROTOCOL.md: the backend spawns

    <java> -cp <jar> org.naurd.media.jsymphonic.headless.HeadlessCli <command> [args]

and reads one JSON object per stdout line (`event` discriminates); stderr is
human-readable noise; exit 0 = success. The JAR rewrites the device DB, so
every invocation is serialized behind a single module-level lock.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

JAR_PATH = Path(__file__).parent / "vendor" / "jsymphonic.jar"
SHIM_MAIN = "org.naurd.media.jsymphonic.headless.HeadlessCli"

# Timeouts (seconds) per PROTOCOL.md — quick DB reads vs. a long batch import.
INFO_TIMEOUT = 120
LIST_TIMEOUT = 120
DEL_TIMEOUT = 600
ADD_TIMEOUT = 3600

# Single-flight: the JAR must never run concurrently — it rewrites OMGAUDIO.
_shim_lock = threading.Lock()

# Test seam: when set, replaces [java, -cp, jar, SHIM_MAIN] as the command
# prefix so tests can substitute e.g. [sys.executable, "fake_shim.py", ...].
SHIM_CMD_PREFIX: list[str] | None = None


class JSymphonicError(RuntimeError):
    pass


def _ensure_java() -> str:
    java = os.environ.get("WALKMAN_BRIDGE_JAVA")
    if java:
        return java
    java = shutil.which("java")
    if not java:
        raise JSymphonicError(
            "java not found in PATH — install a JRE (17+) or set WALKMAN_BRIDGE_JAVA"
        )
    return java


def _ensure_jar() -> Path:
    if not JAR_PATH.exists():
        raise JSymphonicError(
            f"jsymphonic.jar not found at {JAR_PATH}. "
            "Build the forked jar (mvn package) or download it from "
            "github.com/georgewoodall82/jsymphonic and place it in backend/vendor/."
        )
    return JAR_PATH


def _build_cmd(args: list[str]) -> list[str]:
    if SHIM_CMD_PREFIX is not None:
        return [*SHIM_CMD_PREFIX, *args]
    return [_ensure_java(), "-cp", str(_ensure_jar()), SHIM_MAIN, *args]


def _run(
    args: list[str],
    timeout: float,
    on_event: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run one shim command, streaming parsed stdout events. Returns them all."""
    cmd = _build_cmd(args)
    events: list[dict] = []
    with _shim_lock:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            raise JSymphonicError(f"failed to launch shim ({cmd[0]}): {e}") from e

        # Drain stderr on a side thread so a chatty JVM can't fill the pipe
        # and deadlock against our stdout read.
        stderr_lines: list[str] = []
        assert proc.stderr is not None
        stderr_pipe = proc.stderr
        drainer = threading.Thread(
            target=lambda: stderr_lines.extend(l.rstrip("\n") for l in stderr_pipe),
            daemon=True,
        )
        drainer.start()

        # Watchdog: a wedged shim must not hold the lock (and the device) forever.
        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            proc.kill()

        watchdog = threading.Timer(timeout, _kill)
        watchdog.start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    logger.warning("shim: skipping unparseable line: %r", line)
                    continue
                if not isinstance(event, dict):
                    logger.warning("shim: skipping non-object line: %r", line)
                    continue
                events.append(event)
                if on_event is not None:
                    on_event(event)
            proc.wait()
        finally:
            watchdog.cancel()
            if proc.poll() is None:  # e.g. on_event raised mid-stream
                proc.kill()
                proc.wait()
            drainer.join(timeout=5)

        if timed_out.is_set() and proc.returncode != 0:
            raise JSymphonicError(
                f"jsymphonic shim timed out after {timeout:.0f}s: {' '.join(args)}"
            )
        if proc.returncode != 0:
            for event in reversed(events):
                if event.get("event") == "fatal":
                    raise JSymphonicError(
                        str(event.get("message") or "shim reported a fatal error")
                    )
            tail = "\n".join(stderr_lines[-5:]).strip()
            raise JSymphonicError(
                tail or f"jsymphonic shim exited with code {proc.returncode}"
            )
    return events


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def wait_for_idle(timeout: float | None = None) -> bool:
    """Block until no shim invocation is running (True) or the timeout passes
    (False). The desktop launcher uses this to avoid exiting while a JVM is
    mid-way through rewriting the device database."""
    acquired = _shim_lock.acquire(timeout=-1 if timeout is None else timeout)
    if acquired:
        _shim_lock.release()
    return acquired


def device_details(mount: Path) -> dict:
    """Validate the mount via the shim and return its `device` event."""
    events = _run(["info", "--device", str(mount)], INFO_TIMEOUT)
    for event in events:
        if event.get("event") == "device":
            return event
    raise JSymphonicError("shim produced no device event")


def list_tracks(mount: Path) -> list[dict]:
    """Enumerate tracks on the device. Ids are opaque — never persist them."""
    events = _run(["list", "--device", str(mount)], LIST_TIMEOUT)
    tracks: list[dict] = []
    for event in events:
        if event.get("event") == "track":
            tracks.append({
                "id": str(event.get("id", "")),
                "title": event.get("title") or "",
                "artist": event.get("artist") or "",
                "album": event.get("album") or "",
                "duration_seconds": event.get("durationSeconds"),
            })
    if not any(e.get("event") == "listEnd" for e in events):
        logger.warning("shim: list produced no listEnd terminator")
    return tracks


def add_tracks(
    mount: Path, files: list[Path], on_event: Callable[[dict], None]
) -> None:
    """Import a whole batch in ONE shim run — one applyChanges() DB commit."""
    if not files:
        return
    _run(
        ["add", "--device", str(mount), *(str(f) for f in files)],
        ADD_TIMEOUT,
        on_event,
    )


def remove_track(mount: Path, track_id: str) -> None:
    _run(["del", "--device", str(mount), "--id", track_id], DEL_TIMEOUT)
