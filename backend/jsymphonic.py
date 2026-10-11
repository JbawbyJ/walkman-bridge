"""
JSymphonic headless shim wrapper.

Speaks the JSON-lines protocol in docs/PROTOCOL.md: the backend spawns

    <java> -cp <jar> org.naurd.media.jsymphonic.headless.HeadlessCli <command> [args]

and reads one JSON object per stdout line (`event` discriminates); stderr is
human-readable noise; exit 0 = success. The JAR rewrites the device DB, so
every invocation is serialized behind a single module-level lock.
"""
from __future__ import annotations

import enum
import json
import base64
import logging
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
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


@dataclass(frozen=True)
class FileOutcome:
    input_index: int
    path: str
    state: str
    detail: str
    reason_code: str | None = None


@dataclass(frozen=True)
class AddResult:
    files: tuple[FileOutcome, ...]
    needs_reconcile: bool = False


class FatalCode(enum.StrEnum):
    """HeadlessCli fatal `code` values the backend forwards.

    PLAYLIST_REF_MISSING: a dangling track ID blocks playlist writes.
    PLAYLIST_JOURNAL_PENDING: a leftover .jsymphonic-playlist-transaction
    journal blocks reads and writes.
    PLAYLIST_SLOTS_EXHAUSTED: all 2048 lifetime playlist slots are used.

    Any other fatal omits `code`. Missing, non-string, and unrecognized
    values are generic (None). Message text is never parsed for a code.
    Unrecognized strings are dropped, not forwarded, and do not raise.
    """

    PLAYLIST_REF_MISSING = "PLAYLIST_REF_MISSING"
    PLAYLIST_JOURNAL_PENDING = "PLAYLIST_JOURNAL_PENDING"
    PLAYLIST_SLOTS_EXHAUSTED = "PLAYLIST_SLOTS_EXHAUSTED"


def normalize_fatal_code(value) -> str | None:
    """Return a known fatal code, or None for a generic failure."""
    if isinstance(value, FatalCode):
        return value.value
    if not isinstance(value, str):
        return None
    try:
        return FatalCode(value).value
    except ValueError:
        return None


def fatal_code_of(exc) -> str | None:
    """Known fatal code carried by an exception, else None."""
    return normalize_fatal_code(getattr(exc, "code", None))


class JSymphonicError(RuntimeError):
    def __init__(self, message, *, events=(), needs_reconcile=False, code=None, empty_mount=False):
        super().__init__(message)
        self.events = list(events)
        self.needs_reconcile = needs_reconcile
        self.result: AddResult | None = None
        # None means generic: no code, or a code outside FatalCode.
        # `code` is the fatal event's top-level JSON field, never message text.
        self.code = normalize_fatal_code(code)
        self.empty_mount = bool(empty_mount)


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
            "Build the companion fork (https://github.com/JbawbyJ/jsymphonic) "
            "with `mvn package` and place the jar-with-dependencies artifact "
            "at backend/vendor/jsymphonic.jar. The upstream GUI jar does not "
            "include HeadlessCli."
        )
    return JAR_PATH


def _build_cmd(args: list[str]) -> list[str]:
    if SHIM_CMD_PREFIX is not None:
        return [*SHIM_CMD_PREFIX, *args]
    return [_ensure_java(), "-cp", str(_ensure_jar()), SHIM_MAIN, *args]


def _command_mutates(args: list[str]) -> bool:
    """True when this argv can rewrite OMGAUDIO.

    `playlist-recover --inspect` is the read-only half of that command.
    `playlist-recover` without `--inspect`, and `playlist-repair`, write.
    """
    if not args:
        return False
    command = args[0]
    if command in {"add", "del", "playlist-create", "playlist-update", "playlist-delete", "playlist-repair"}:
        return True
    if command == "playlist-recover":
        return "--inspect" not in args
    return False


def _run(
    args: list[str],
    timeout: float,
    on_event: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run one shim command, streaming parsed stdout events. Returns them all."""
    cmd = _build_cmd(args)
    events: list[dict] = []
    mutating = _command_mutates(args)
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
        stderr_lines: deque[str] = deque(maxlen=200)
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
            try:
                proc.kill()
            except OSError:
                pass  # Process may have exited between the timer and kill.

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
        except Exception as exc:
            raise JSymphonicError(
                f"Shim output processing failed: {exc}", events=events,
                needs_reconcile=mutating,
            ) from exc
        finally:
            watchdog.cancel()
            if proc.poll() is None:  # e.g. on_event raised mid-stream
                proc.kill()
                proc.wait()
            drainer.join(timeout=5)
            proc.stdout.close()
            proc.stderr.close()

        if timed_out.is_set() and proc.returncode != 0:
            raise JSymphonicError(
                f"jsymphonic shim timed out after {timeout:.0f}s: {' '.join(args)}",
                events=events, needs_reconcile=mutating,
            )
        if proc.returncode != 0:
            for event in reversed(events):
                if event.get("event") == "fatal":
                    empty_mount = event.get("emptyMount") is True
                    raise JSymphonicError(
                        str(event.get("message") or "shim reported a fatal error"),
                        events=events, needs_reconcile=mutating and not empty_mount,
                        code=event.get("code"),
                        empty_mount=empty_mount,
                    )
            tail = "\n".join(list(stderr_lines)[-5:]).strip()
            raise JSymphonicError(
                tail or f"jsymphonic shim exited with code {proc.returncode}",
                events=events, needs_reconcile=mutating,
            )
    return events


def _require_terminal(events: list[dict], terminal: str, *, mutating=False) -> None:
    for event in events:
        if event.get("event") == "fatal" or event.get("error"):
            raise JSymphonicError(
                str(event.get("message") or event.get("error") or "Shim reported a fatal error"),
                events=events, needs_reconcile=mutating,
                code=event.get("code") if event.get("event") == "fatal" else None,
            )
    if not events or events[-1].get("event") != terminal:
        raise JSymphonicError(
            f"Shim produced no final {terminal} terminal marker",
            events=events, needs_reconcile=mutating,
        )


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
    _require_terminal(events, "device")
    for event in events:
        if event.get("event") == "device":
            if event.get("ok") is not True:
                raise JSymphonicError("Shim did not validate the device", events=events)
            return event
    raise JSymphonicError("shim produced no device event")


def list_tracks(mount: Path) -> list[dict]:
    """Enumerate tracks on the device. Ids are opaque — never persist them."""
    events = _run(["list", "--device", str(mount)], LIST_TIMEOUT)
    _require_terminal(events, "listEnd")
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
    count = events[-1].get("count")
    if type(count) is not int or count != len(tracks):
        raise JSymphonicError("Shim listEnd count does not match returned tracks", events=events)
    return tracks


def add_tracks(
    mount: Path, files: list[Path], on_event: Callable[[dict], None]
) -> AddResult:
    """Import a whole batch in ONE shim run — one applyChanges() DB commit."""
    if not files:
        return AddResult(())
    try:
        events = _run(
            ["add", "--device", str(mount), *(str(f) for f in files)],
            ADD_TIMEOUT,
            on_event,
        )
        _require_terminal(events, "done", mutating=True)
    except JSymphonicError as exc:
        # The legacy protocol only reports aggregate step errors. Neither a
        # file event nor 100% progress proves its database write committed.
        exc.result = AddResult(tuple(
            FileOutcome(index, str(path), "unknown" if exc.needs_reconcile else "failed", str(exc),
                        "device_outcome_unknown" if exc.needs_reconcile else "shim_unavailable")
            for index, path in enumerate(files)
        ), needs_reconcile=exc.needs_reconcile)
        raise
    return AddResult(tuple(
        FileOutcome(index, str(path), "transferred", "Device database update completed")
        for index, path in enumerate(files)
    ))


def remove_track(mount: Path, track_id: str) -> None:
    events = _run(["del", "--device", str(mount), "--id", track_id], DEL_TIMEOUT)
    _require_terminal(events, "done", mutating=True)


def _playlist_row(event, events, *, mutating=False):
    playlist_id, name, ids = event.get('id'), event.get('name'), event.get('trackIds')
    if (not isinstance(playlist_id, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', playlist_id)
            or not isinstance(name, str) or not isinstance(ids, list)
            or any(not isinstance(key, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', key) for key in ids)):
        raise JSymphonicError('Shim returned an invalid playlist row', events=events, needs_reconcile=mutating)
    return {'id': playlist_id, 'name': name, 'track_ids': ids}


def list_playlists(mount: Path) -> list[dict]:
    events = _run(['playlists', '--device', str(mount)], LIST_TIMEOUT)
    _require_terminal(events, 'playlistsEnd')
    rows = [_playlist_row(event, events) for event in events if event.get('event') == 'playlist']
    count = events[-1].get('count')
    if type(count) is not int or count != len(rows) or len({row['id'] for row in rows}) != len(rows):
        raise JSymphonicError('Shim playlistsEnd count or playlist IDs are invalid', events=events)
    return rows


def _playlist_mutation(args):
    events = _run(args, DEL_TIMEOUT)
    _require_terminal(events, 'done', mutating=True)
    rows = [_playlist_row(event, events, mutating=True) for event in events if event.get('event') == 'playlist']
    if len(rows) != 1:
        raise JSymphonicError('Shim did not return the changed playlist', events=events, needs_reconcile=True)
    return rows[0]


def _device_playlist_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', value):
        raise ValueError('Invalid device playlist or track ID')
    return value


def _device_playlist_tracks(ids):
    if not isinstance(ids, list) or len(ids) > 200:
        raise ValueError('A playlist can contain at most 200 tracks')
    for key in ids:
        _device_playlist_id(key)
    return ids


def create_playlist(mount: Path, name: str, track_ids: list[str]) -> dict:
    from media_store import playlist_name
    # Windows Java's launcher can replace non-ANSI argv characters before main().
    encoded = base64.b64encode(playlist_name(name).encode('utf-8')).decode('ascii')
    return _playlist_mutation(['playlist-create', '--device', str(mount), '--name-base64',
        encoded, *_device_playlist_tracks(track_ids)])


def update_playlist(mount: Path, playlist_id: str, name: str | None = None, track_ids: list[str] | None = None) -> dict:
    from media_store import playlist_name
    args = ['playlist-update', '--device', str(mount), '--id', _device_playlist_id(playlist_id)]
    if name is not None:
        args.extend(['--name-base64', base64.b64encode(playlist_name(name).encode('utf-8')).decode('ascii')])
    if track_ids is not None:
        args.extend(['--tracks', ','.join(_device_playlist_tracks(track_ids))])
    if name is None and track_ids is None:
        raise ValueError('Provide a playlist name or track IDs')
    row = _playlist_mutation(args)
    if row['id'] != playlist_id:
        raise JSymphonicError('Shim changed a different playlist', needs_reconcile=True)
    return row


def delete_playlist(mount: Path, playlist_id: str) -> None:
    events = _run(['playlist-delete', '--device', str(mount), '--id', _device_playlist_id(playlist_id)], DEL_TIMEOUT)
    _require_terminal(events, 'done', mutating=True)


def _reject_fatal_events(events: list[dict], *, mutating: bool) -> None:
    """Fail when a recovery command embeds a fatal on an otherwise zero exit.

    The fatal `code` is that event's top-level field. Message text is not a code.
    """
    for event in events:
        if event.get("event") == "fatal" or event.get("error"):
            empty_mount = event.get("emptyMount") is True
            raise JSymphonicError(
                str(event.get("message") or event.get("error") or "Shim reported a fatal error"),
                events=events, needs_reconcile=mutating and not empty_mount,
                code=event.get("code") if event.get("event") == "fatal" else None,
                empty_mount=empty_mount,
            )


_JOURNAL_STATES = frozenset({"committed", "uncommitted", "none"})
_RECOVER_OUTCOMES = frozenset({"rolled_forward", "discarded", "none"})
_RECOVERY_SKIP_EVENTS = frozenset({"step"})


def _explicit_value(events: list[dict], key: str, allowed: frozenset[str]):
    """Last top-level `key` whose value is in `allowed`, else None.

    Step events are skipped: their `state` means started/finished, not the
    journal. A present but unknown value is null, not a guess from `message`.
    """
    found = None
    for event in events:
        if event.get("event") in _RECOVERY_SKIP_EVENTS or key not in event:
            continue
        value = event[key]
        found = value if isinstance(value, str) and value in allowed else None
    return found


def _explicit_ids(events: list[dict], key: str):
    found = None
    seen = False
    for event in events:
        if event.get("event") in _RECOVERY_SKIP_EVENTS or key not in event:
            continue
        seen = True
        value = event[key]
        if (isinstance(value, list) and all(isinstance(item, str) and re.fullmatch(r"[1-9][0-9]{0,9}", item) for item in value)):
            found = list(value)
        else:
            found = None
    return found if seen else None


def _explicit_count(events: list[dict], key: str):
    found = None
    seen = False
    for event in events:
        if event.get("event") in _RECOVERY_SKIP_EVENTS or key not in event:
            continue
        seen = True
        value = event[key]
        found = value if type(value) is int and value >= 0 else None
    return found if seen else None


def _journal_covers(events: list[dict]):
    relevant = [event for event in events if event.get("event") not in _RECOVERY_SKIP_EVENTS]
    if not any("playlistIds" in event or "trackIds" in event for event in relevant):
        return None
    return {
        "playlist_ids": _explicit_ids(events, "playlistIds"),
        "track_ids": _explicit_ids(events, "trackIds"),
    }


def inspect_playlist_journal(mount: Path) -> dict:
    """Read-only `playlist-recover --inspect`.

    `state` is the shim's top-level field: committed, uncommitted, or none.
    d4fbc94 omits it; a missing or unknown value stays null. `exists` follows
    `state` only (false for none, true when a journal state is known).
    """
    events = _run(["playlist-recover", "--device", str(mount), "--inspect"], INFO_TIMEOUT)
    _reject_fatal_events(events, mutating=False)
    state = _explicit_value(events, "state", _JOURNAL_STATES)
    exists = None if state is None else state != "none"
    return {"exists": exists, "state": state, "covers": _journal_covers(events)}


def recover_playlist_journal(mount: Path) -> dict:
    """Mutating `playlist-recover`.

    `outcome` is the shim's top-level field: rolled_forward, discarded, or none.
    A missing or unknown value stays null. Message text is never read.
    """
    events = _run(["playlist-recover", "--device", str(mount)], DEL_TIMEOUT)
    _reject_fatal_events(events, mutating=True)
    return {"outcome": _explicit_value(events, "outcome", _RECOVER_OUTCOMES)}


def repair_playlists(mount: Path) -> dict:
    """Mutating `playlist-repair`.

    `prunedCount` of 0 is a real result. `emptyMount: true` is a refusal and
    is not reported as success. Both are top-level fields, not message text.
    """
    events = _run(["playlist-repair", "--device", str(mount)], DEL_TIMEOUT)
    _reject_fatal_events(events, mutating=True)
    if any(event.get("emptyMount") is True for event in events):
        raise JSymphonicError(
            "playlist-repair refused because the mount is empty",
            events=events, needs_reconcile=False, empty_mount=True,
        )
    return {
        "pruned_count": _explicit_count(events, "prunedCount"),
        "pruned_track_ids": _explicit_ids(events, "prunedTrackIds"),
        "playlist_ids": _explicit_ids(events, "playlistIds"),
    }
