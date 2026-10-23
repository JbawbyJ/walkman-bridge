"""A failing on_event callback must never kill the running shim.

The callback is local bookkeeping (write_device's job.log commits to SQLite).
The shim is the sole OMGAUDIO writer, so only its watchdog may stop it.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import jsymphonic
from jsymphonic import JSymphonicError

# The shim writes this beside the device only after its slow "commit" step.
MARKER = "committed.marker"


def scripted(monkeypatch, *steps):
    """One-line `python -c` shim. argv after -c is the real shim argv, so
    sys.argv[3] is the --device mount for add and list alike."""
    code = "; ".join((
        "import json, pathlib, sys, time",
        "emit = lambda event: print(json.dumps(event), flush=True)",
        *steps,
    ))
    monkeypatch.setattr(jsymphonic, "SHIM_CMD_PREFIX", [sys.executable, "-c", code])


def emit(event):
    return f"emit({event!r})"


COMMIT = f"time.sleep(1); pathlib.Path(sys.argv[3], {MARKER!r}).write_text('ok')"


@pytest.fixture
def spawned(monkeypatch):
    """Record each shim process so a test can see how it ended."""
    procs = []

    class Recording(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            procs.append(self)

    monkeypatch.setattr(jsymphonic.subprocess, "Popen", Recording)
    return procs


def failing_callback():
    calls = []

    def on_event(event):
        calls.append(event)
        raise sqlite3.OperationalError("database or disk is full")

    return calls, on_event


def test_add_callback_failure_lets_the_shim_finish_and_stays_uncertain(monkeypatch, tmp_path, spawned):
    scripted(monkeypatch,
             emit({"event": "start", "files": 1}),
             emit({"event": "progress", "step": "transfer", "percent": 50.0}),
             COMMIT,
             emit({"event": "done"}))
    calls, on_event = failing_callback()
    with pytest.raises(JSymphonicError, match="Device progress could not be recorded") as error:
        jsymphonic.add_tracks(tmp_path, [tmp_path / "a.mp3"], on_event)
    # The shim ran to its own clean exit instead of being killed mid-rewrite.
    assert (tmp_path / MARKER).read_text() == "ok"
    assert [proc.returncode for proc in spawned] == [0]
    assert [event["event"] for event in error.value.events] == ["start", "progress", "done"]
    assert len(calls) == 1  # reporting stops after the first failure
    # Unrecorded is not proven: the write must be reconciled, never retried.
    assert error.value.needs_reconcile is True
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert [(row.state, row.reason_code) for row in error.value.result.files] == [("unknown", "device_outcome_unknown")]
    assert jsymphonic.job_needs_reconcile(error.value, True)
    assert jsymphonic.wait_for_idle(0)


def test_read_callback_failure_is_a_definite_failure(monkeypatch, tmp_path, spawned):
    scripted(monkeypatch,
             emit({"event": "track", "id": "1", "title": "Alpha"}),
             COMMIT,
             emit({"event": "listEnd", "count": 1}))
    calls, on_event = failing_callback()
    with pytest.raises(JSymphonicError, match="Device progress could not be recorded") as error:
        jsymphonic._run(["list", "--device", str(tmp_path)], jsymphonic.LIST_TIMEOUT, on_event)
    assert (tmp_path / MARKER).exists()
    assert [proc.returncode for proc in spawned] == [0]
    assert len(calls) == 1
    assert error.value.needs_reconcile is False
    assert not jsymphonic.job_needs_reconcile(error.value, False)


def test_fatal_exit_after_callback_failure_keeps_its_classification(monkeypatch, tmp_path, spawned):
    scripted(monkeypatch,
             emit({"event": "start", "files": 1}),
             COMMIT,
             emit({"event": "fatal", "message": "Device file is locked", "code": "DEVICE_FILE_LOCKED",
                   "path": "OMGAUDIO/10F00/10000001.OMA"}),
             "sys.exit(1)")
    calls, on_event = failing_callback()
    with pytest.raises(JSymphonicError, match="Device file is locked") as error:
        jsymphonic.add_tracks(tmp_path, [tmp_path / "a.mp3"], on_event)
    assert [proc.returncode for proc in spawned] == [1]
    # The shim's own fatal decides; the callback failure is only chained.
    assert error.value.code == "DEVICE_FILE_LOCKED"
    assert error.value.path == "OMGAUDIO/10F00/10000001.OMA"
    assert error.value.needs_reconcile is False
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert [row.state for row in error.value.result.files] == ["failed"]


def test_watchdog_still_bounds_a_shim_after_callback_failure(monkeypatch, tmp_path, spawned):
    scripted(monkeypatch, emit({"event": "start", "files": 1}), "time.sleep(30)", emit({"event": "done"}))
    monkeypatch.setattr(jsymphonic, "ADD_TIMEOUT", 2)
    calls, on_event = failing_callback()
    start = time.monotonic()
    with pytest.raises(JSymphonicError, match="timed out") as error:
        jsymphonic.add_tracks(tmp_path, [tmp_path / "a.mp3"], on_event)
    assert time.monotonic() - start < 15
    assert error.value.needs_reconcile is True
    assert spawned[0].returncode != 0
    assert len(calls) <= 1
    assert jsymphonic.wait_for_idle(0)
