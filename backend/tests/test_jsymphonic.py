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


def scripted(monkeypatch, events, exit_code=0):
    code = "import json,sys; events=" + repr(events) + "; [print(json.dumps(e),flush=True) for e in events]; sys.exit(" + str(exit_code) + ")"
    monkeypatch.setattr(jsymphonic, "SHIM_CMD_PREFIX", [sys.executable, "-c", code])


def test_list_requires_terminal_marker(monkeypatch):
    scripted(monkeypatch, [{"event": "track", "id": "1"}])
    with pytest.raises(JSymphonicError, match="listEnd"):
        jsymphonic.list_tracks(Path("X:/"))


def test_list_requires_correct_terminal_count(monkeypatch):
    scripted(monkeypatch, [{"event": "listEnd", "count": 1}])
    with pytest.raises(JSymphonicError, match="count"):
        jsymphonic.list_tracks(Path("X:/"))


@pytest.mark.parametrize("events", [
    [{"event": "start", "files": 1}],
    [{"event": "fatal", "message": "failed"}, {"event": "done"}],
    [{"event": "step", "error": "copy failed"}, {"event": "done"}],
])
def test_add_never_succeeds_without_clean_terminal_evidence(monkeypatch, events):
    scripted(monkeypatch, events)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.add_tracks(Path("X:/"), [Path("a.mp3")], lambda _: None)
    assert error.value.needs_reconcile
    assert error.value.result.files[0].state == "unknown"


def test_done_before_nonzero_exit_is_not_committed_success(monkeypatch):
    scripted(monkeypatch, [{"event": "file", "step": "transfer", "name": "a.mp3"}, {"event": "done"}], exit_code=1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.add_tracks(Path("X:/"), [Path("a.mp3"), Path("b.mp3")], lambda _: None)
    assert [row.state for row in error.value.result.files] == ["unknown", "unknown"]
    assert error.value.events[-1] == {"event": "done"}


def test_add_results_preserve_duplicate_basename_input_identity(shim, tmp_path):
    shim("happy_add")
    files = [tmp_path / "one" / "song.mp3", tmp_path / "two" / "song.mp3"]
    result = jsymphonic.add_tracks(tmp_path, files, lambda _: None)
    assert [(row.input_index, row.path, row.state) for row in result.files] == [(index, str(path), "transferred") for index, path in enumerate(files)]
    assert result.needs_reconcile is False


def test_process_launch_failure_is_definite_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(jsymphonic, "SHIM_CMD_PREFIX", [str(tmp_path / "missing-executable")])
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.add_tracks(tmp_path, [tmp_path / "a.mp3"], lambda _: None)
    assert error.value.needs_reconcile is False
    assert error.value.result.files[0].state == "failed"


def test_callback_failure_retains_unknown_outcomes(shim, tmp_path):
    shim("happy_add")

    def fail(event):
        raise OSError("job persistence unavailable")

    with pytest.raises(JSymphonicError, match="job persistence unavailable") as error:
        jsymphonic.add_tracks(tmp_path, [tmp_path / "a.mp3"], fail)
    assert error.value.result.files[0].state == "unknown"
    assert jsymphonic.wait_for_idle(0)


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
    assert "start" in kinds
    assert kinds[-1] == "done"
    transfer_files = [e for e in seen
                      if e["event"] == "file" and e.get("step") == "transfer"]
    assert len(transfer_files) == 2
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
    with pytest.raises(JSymphonicError, match="JbawbyJ/jsymphonic"):
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


def test_add_partial_errors_raises_with_step_error(shim, tmp_path):
    """The real shim exits 1 with a fatal summary when any step reported an
    error — the wrapper must surface that text, never silent success."""
    shim("add_partial_errors")
    events: list[dict] = []
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    with pytest.raises(JSymphonicError) as exc:
        jsymphonic.add_tracks(tmp_path, [src], events.append)
    assert "1 file could not be copied" in str(exc.value)
    # The step events streamed out before the failure surfaced.
    assert any(e.get("event") == "step" and e.get("error") for e in events)


def test_del_failed_raises_with_error_text(shim, tmp_path):
    shim("del_failed")
    with pytest.raises(JSymphonicError) as exc:
        jsymphonic.remove_track(tmp_path, "1")
    assert "could not be deleted" in str(exc.value)


FATAL_CODES = (
    "PLAYLIST_REF_MISSING",
    "PLAYLIST_JOURNAL_PENDING",
    "PLAYLIST_SLOTS_EXHAUSTED",
)


def test_fatal_codes_are_defined_once():
    assert tuple(code.value for code in jsymphonic.FatalCode) == FATAL_CODES


@pytest.mark.parametrize("code", FATAL_CODES)
def test_known_fatal_code_passes_through(monkeypatch, code):
    scripted(monkeypatch, [{"event": "fatal", "message": "playlist blocked", "code": code}], exit_code=1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.list_playlists(Path("X:/"))
    assert error.value.code == code
    assert str(error.value) == "playlist blocked"


def test_fatal_without_code_stays_generic(monkeypatch):
    scripted(monkeypatch, [{"event": "fatal", "message": "device database is corrupt"}], exit_code=1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.list_playlists(Path("X:/"))
    assert error.value.code is None
    assert str(error.value) == "device database is corrupt"


def test_message_text_does_not_invent_fatal_code(monkeypatch):
    scripted(monkeypatch, [{
        "event": "fatal",
        "message": "blocked: PLAYLIST_REF_MISSING PLAYLIST_JOURNAL_PENDING PLAYLIST_SLOTS_EXHAUSTED",
    }], exit_code=1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.list_playlists(Path("X:/"))
    assert error.value.code is None


def test_unknown_fatal_code_is_generic_and_does_not_crash(monkeypatch):
    scripted(monkeypatch, [{"event": "fatal", "message": "nope", "code": "PLAYLIST_OTHER"}], exit_code=1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.list_playlists(Path("X:/"))
    assert error.value.code is None
    assert str(error.value) == "nope"


@pytest.mark.parametrize("raw", [None, "", 12, True, ["PLAYLIST_REF_MISSING"]])
def test_non_string_fatal_code_is_generic(monkeypatch, raw):
    scripted(monkeypatch, [{"event": "fatal", "message": "nope", "code": raw}], exit_code=1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.list_playlists(Path("X:/"))
    assert error.value.code is None


def test_step_error_does_not_adopt_a_fatal_code(monkeypatch):
    scripted(monkeypatch, [
        {"event": "step", "error": "copy failed", "code": "PLAYLIST_REF_MISSING"},
        {"event": "done"},
    ])
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.add_tracks(Path("X:/"), [Path("a.mp3")], lambda _: None)
    assert error.value.code is None
