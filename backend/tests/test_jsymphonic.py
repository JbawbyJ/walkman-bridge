"""
jsymphonic.py wrapper tests — all run against tests/fake_shim.py, substituted
for the real `java -cp ...` invocation via the SHIM_CMD_PREFIX test seam.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

import jsymphonic
from jsymphonic import JSymphonicError

FAKE_SHIM = Path(__file__).parent / "fake_shim.py"


@pytest.fixture
def shim(monkeypatch):
    """Returns a selector that points jsymphonic at a fake-shim scenario."""
    def use(scenario: str) -> None:
        monkeypatch.setattr(
            jsymphonic, "SHIM_CMD_PREFIX", [sys.executable, str(FAKE_SHIM), scenario]
        )
    return use


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #

def test_device_details_happy(shim):
    shim("happy_info")
    details = jsymphonic.device_details(Path("X:/"))
    assert details["ok"] is True
    assert details["trackCount"] == 2


def test_list_tracks_happy(shim):
    shim("happy_list")
    tracks = jsymphonic.list_tracks(Path("X:/"))
    assert [t["id"] for t in tracks] == ["1", "2"]
    assert tracks[0]["title"] == "Alpha"
    assert tracks[1]["duration_seconds"] == 200


def test_add_tracks_streams_events(shim, tmp_path):
    shim("happy_add")
    seen: list[dict] = []
    files = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
    jsymphonic.add_tracks(Path("X:/"), files, seen.append)
    kinds = [e["event"] for e in seen]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert kinds.count("file") == 2
    assert "progress" in kinds


def test_remove_track_happy(shim):
    shim("happy_del")
    jsymphonic.remove_track(Path("X:/"), "7")  # no raise = pass


def test_command_args_passed_through(shim):
    shim("echo_args")
    events = jsymphonic._run(["del", "--device", "X:/", "--id", "7"], timeout=30)
    assert events[0]["argv"] == ["del", "--device", "X:/", "--id", "7"]


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #

def test_fatal_event_message_wins(shim):
    shim("fatal")
    with pytest.raises(JSymphonicError, match="device database is corrupt"):
        jsymphonic.list_tracks(Path("X:/"))


def test_nonzero_exit_uses_stderr_tail(shim):
    shim("stderr_fail")
    with pytest.raises(JSymphonicError, match="boom"):
        jsymphonic.list_tracks(Path("X:/"))


def test_unparseable_lines_skipped(shim):
    shim("garbage")
    tracks = jsymphonic.list_tracks(Path("X:/"))
    assert [t["id"] for t in tracks] == ["1"]


def test_timeout_kills_process(shim, monkeypatch):
    shim("hang")
    monkeypatch.setattr(jsymphonic, "LIST_TIMEOUT", 2)
    start = time.monotonic()
    with pytest.raises(JSymphonicError, match="timed out"):
        jsymphonic.list_tracks(Path("X:/"))
    # The fake shim sleeps 30s; well under that means the kill worked.
    assert time.monotonic() - start < 15


def test_missing_jar_error_is_helpful(monkeypatch, tmp_path):
    monkeypatch.setattr(jsymphonic, "SHIM_CMD_PREFIX", None)
    monkeypatch.setattr(jsymphonic, "JAR_PATH", tmp_path / "vendor" / "jsymphonic.jar")
    monkeypatch.setenv("WALKMAN_BRIDGE_JAVA", "java")  # skip PATH lookup
    with pytest.raises(JSymphonicError, match="vendor"):
        jsymphonic.list_tracks(Path("X:/"))


def test_java_env_override(monkeypatch, tmp_path):
    jar = tmp_path / "jsymphonic.jar"
    jar.write_bytes(b"")
    monkeypatch.setattr(jsymphonic, "SHIM_CMD_PREFIX", None)
    monkeypatch.setattr(jsymphonic, "JAR_PATH", jar)
    monkeypatch.setenv("WALKMAN_BRIDGE_JAVA", r"C:\custom\java.exe")
    cmd = jsymphonic._build_cmd(["info", "--device", "X:/"])
    assert cmd[:3] == [r"C:\custom\java.exe", "-cp", str(jar)]
    assert cmd[3] == jsymphonic.SHIM_MAIN


# --------------------------------------------------------------------------- #
# Single-flight lock
# --------------------------------------------------------------------------- #

def test_single_flight_lock_serializes(shim):
    shim("stamp")
    results: list[list[dict]] = []
    results_lock = threading.Lock()

    def worker() -> None:
        events = jsymphonic._run(["stamp"], timeout=30)
        with results_lock:
            results.append(events)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    (a_start, a_end), (b_start, b_end) = [
        (ev[0]["t"], ev[1]["t"]) for ev in results
    ]
    # Serialized: one run's whole interval precedes the other's.
    assert a_end <= b_start or b_end <= a_start
