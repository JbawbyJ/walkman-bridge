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
    PLAYLIST_LIBRARY_NOT_LOADED: playlist repair refused because the mount
    is empty or the library is not loaded. Nothing was written.
    DEVICE_FILE_LOCKED: a device file is locked. The device is still on its
    previous snapshot and no journal is left. The fatal event carries a
    top-level `path`, an opaque string relative to the mount. It is copied
    unchanged and is never joined or reformatted.
    DEVICE_ROLLBACK_FAILED: rollback did not restore a known snapshot, so
    the device state is uncertain.

    Any other fatal omits `code`. Missing, non-string, and unrecognized
    values are generic (None). Message text is never parsed for a code.
    Unrecognized strings are dropped, not forwarded, and do not raise.
    """

    PLAYLIST_REF_MISSING = "PLAYLIST_REF_MISSING"
    PLAYLIST_JOURNAL_PENDING = "PLAYLIST_JOURNAL_PENDING"
    PLAYLIST_SLOTS_EXHAUSTED = "PLAYLIST_SLOTS_EXHAUSTED"
    PLAYLIST_LIBRARY_NOT_LOADED = "PLAYLIST_LIBRARY_NOT_LOADED"
    DEVICE_FILE_LOCKED = "DEVICE_FILE_LOCKED"
    DEVICE_ROLLBACK_FAILED = "DEVICE_ROLLBACK_FAILED"


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


def fatal_path_of(exc) -> str | None:
    """Top-level fatal `path` when it is a string, else None.

    Message text and nested objects are not a path.
    """
    path = getattr(exc, "path", None)
    return path if isinstance(path, str) else None


def _string_path(value):
    return value if isinstance(value, str) else None


def _fatal_needs_reconcile(mutating: bool, code) -> bool:
    """Definite non-writes stay false. A failed rollback stays uncertain."""
    normalized = normalize_fatal_code(code)
    if normalized == FatalCode.DEVICE_ROLLBACK_FAILED:
        return True
    if normalized in {FatalCode.PLAYLIST_LIBRARY_NOT_LOADED, FatalCode.DEVICE_FILE_LOCKED}:
        return False
    return bool(mutating)


def job_needs_reconcile(exc, writing: bool) -> bool:
    """Job flag for a failed device operation.

    PLAYLIST_LIBRARY_NOT_LOADED and DEVICE_FILE_LOCKED leave the previous
    snapshot in place. DEVICE_ROLLBACK_FAILED leaves the device uncertain.
    Any other failure is reconciled only after a write has started.
    """
    code = fatal_code_of(exc)
    if code == FatalCode.DEVICE_ROLLBACK_FAILED.value:
        return True
    if code in {FatalCode.PLAYLIST_LIBRARY_NOT_LOADED.value, FatalCode.DEVICE_FILE_LOCKED.value}:
        return False
    if not writing:
        return False
    return getattr(exc, "needs_reconcile", True) is not False


class JSymphonicError(RuntimeError):
    def __init__(self, message, *, events=(), needs_reconcile=False, code=None, path=None):
        super().__init__(message)
        self.events = list(events)
        self.needs_reconcile = needs_reconcile
        self.result: AddResult | None = None
        # None means generic: no code, or a code outside FatalCode.
        # `code` is the fatal event's top-level JSON field, never message text.
        self.code = normalize_fatal_code(code)
        # Top-level fatal `path` only. Non-strings are dropped.
        self.path = _string_path(path)


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
                    code = event.get("code")
                    raise JSymphonicError(
                        str(event.get("message") or "shim reported a fatal error"),
                        events=events, needs_reconcile=_fatal_needs_reconcile(mutating, code),
                        code=code, path=event.get("path"),
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
            code = event.get("code") if event.get("event") == "fatal" else None
            path = event.get("path") if event.get("event") == "fatal" else None
            raise JSymphonicError(
                str(event.get("message") or event.get("error") or "Shim reported a fatal error"),
                events=events, needs_reconcile=_fatal_needs_reconcile(mutating, code),
                code=code, path=path,
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


_JOURNAL_STATES = frozenset({"committed", "uncommitted", "none"})
_RECOVER_OUTCOMES = frozenset({"rolled_forward", "discarded", "none"})


def _last_event(events: list[dict], name: str):
    found = None
    for event in events:
        if event.get("event") == name:
            found = event
    return found


def _enum_field(event, key: str, allowed: frozenset[str]):
    """Known value of one top-level field on one event, else None."""
    if not event or key not in event:
        return None
    value = event[key]
    if isinstance(value, str) and value in allowed:
        return value
    return None


def _journal_covers(event):
    """`covers.files` from playlistJournal.files, or None.

    Each string is opaque and relative to the OMGAUDIO folder (jsymphonic #4
    @ 857b91e). The list is copied as-is. A missing field or any non-list /
    non-string value is null. playlistIds and trackIds are not coverage.
    """
    if not event or "files" not in event:
        return None
    files = event["files"]
    if not isinstance(files, list) or any(not isinstance(item, str) for item in files):
        return None
    return {"files": list(files)}


def _summary_ids(value):
    """Normalize playlistRepair summary ids to strings.

    jsymphonic 5763643 emits JSON numbers. Playlist rows use strings.
    """
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if type(item) is int and item > 0:
            text = str(item)
        elif isinstance(item, str):
            text = item
        else:
            return None
        if not re.fullmatch(r"[1-9][0-9]{0,9}", text):
            return None
        out.append(text)
    return out


def inspect_playlist_journal(mount: Path) -> dict:
    """Read-only `playlist-recover --device <mount> --inspect`.

    `state` is read only from the `playlistJournal` event (jsymphonic #4
    @ 5763643). Inspect does not emit `outcome`. A missing or unknown
    `state` is null. `exists` follows `state`.
    """
    events = _run(["playlist-recover", "--device", str(mount), "--inspect"], INFO_TIMEOUT)
    _require_terminal(events, "done", mutating=False)
    journal = _last_event(events, "playlistJournal")
    state = _enum_field(journal, "state", _JOURNAL_STATES)
    exists = None if state is None else state != "none"
    return {"exists": exists, "state": state, "covers": _journal_covers(journal)}


def recover_playlist_journal(mount: Path) -> dict:
    """Mutating `playlist-recover --device <mount>`.

    `outcome` is the top-level field on the `playlistJournal` event, not on
    `done` and not message text. Values: rolled_forward, discarded, none.
    """
    events = _run(["playlist-recover", "--device", str(mount)], DEL_TIMEOUT)
    _require_terminal(events, "done", mutating=True)
    outcome = _enum_field(_last_event(events, "playlistJournal"), "outcome", _RECOVER_OUTCOMES)
    return {"outcome": outcome}


def repair_playlists(mount: Path) -> dict:
    """Mutating `playlist-repair --device <mount>`.

    The `playlistRepair` summary comes before playlist rows and `done`.
    `prunedCount` counts removed member references and is not checked
    against `len(prunedTrackIds)`. Summary ids are JSON numbers and are
    returned as strings. An empty mount is the fatal code
    `PLAYLIST_LIBRARY_NOT_LOADED`, raised by `_run`.
    """
    events = _run(["playlist-repair", "--device", str(mount)], DEL_TIMEOUT)
    _require_terminal(events, "done", mutating=True)
    summary = _last_event(events, "playlistRepair") or {}
    count = summary.get("prunedCount") if summary else None
    return {
        "pruned_count": count if type(count) is int and count >= 0 else None,
        "pruned_track_ids": _summary_ids(summary.get("prunedTrackIds")) if "prunedTrackIds" in summary else None,
        "playlist_ids": _summary_ids(summary.get("playlistIds")) if "playlistIds" in summary else None,
    }
