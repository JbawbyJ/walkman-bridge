from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import device
from operations import BusyError, DrainingError, OperationCoordinator


def test_drain_tracks_admitted_work_and_refuses_new_import():
    coordinator = OperationCoordinator()
    ticket = coordinator.admit("import", job_id="one")
    with pytest.raises(BusyError):
        coordinator.admit("import")
    snapshot = coordinator.begin_drain()
    assert snapshot["draining"] and snapshot["busy"]
    assert snapshot["active"] == [{"kind": "import", "job_id": "one"}]
    with pytest.raises(DrainingError):
        coordinator.admit("backup")
    assert coordinator.wait_drained(0) is False
    coordinator.finish(ticket)
    assert coordinator.wait_drained(0) is True


def test_device_gate_serializes_and_drain_does_not_block(tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    coordinator = OperationCoordinator()
    identity = device.capture_device_identity(tmp_path)
    first = coordinator.admit("backup", device=identity)
    second = coordinator.admit("list", device=identity)
    entered = Event()
    release = Event()
    second_entered = Event()

    def run(ticket, signal, hold=False):
        try:
            with coordinator.device_session(ticket) as mount:
                assert mount == tmp_path
                signal.set()
                if hold:
                    assert release.wait(5)
        finally:
            coordinator.finish(ticket)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, first, entered, True)
        assert entered.wait(5)
        b = pool.submit(run, second, second_entered)
        assert not second_entered.wait(0.05)
        assert coordinator.begin_drain()["busy"]
        assert not coordinator.wait_drained(0)
        release.set()
        a.result(5)
        b.result(5)
    assert second_entered.is_set()
    assert coordinator.wait_drained(0)


def test_replaced_volume_is_rejected_at_gate(monkeypatch, tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    coordinator = OperationCoordinator()
    identity = device.capture_device_identity(tmp_path)
    ticket = coordinator.admit("delete", device=identity)
    monkeypatch.setattr(device, "_volume_identity", lambda path: "replacement-volume")
    with pytest.raises(device.DeviceChangedError):
        with coordinator.device_session(ticket):
            pytest.fail("operation reached a replacement volume")
    coordinator.finish(ticket)


def test_replacement_during_device_work_requires_reconciliation(monkeypatch, tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    coordinator = OperationCoordinator()
    ticket = coordinator.admit("transfer", device=device.capture_device_identity(tmp_path))
    with pytest.raises(device.DeviceChangedError) as error:
        with coordinator.device_session(ticket):
            monkeypatch.setattr(device, "_volume_identity", lambda path: "replacement-volume")
    assert error.value.needs_reconcile is True
    coordinator.finish(ticket)


def test_cannot_finish_a_session_before_device_work_ends(tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    coordinator = OperationCoordinator()
    ticket = coordinator.admit("backup", device=device.capture_device_identity(tmp_path))
    with coordinator.device_session(ticket):
        with pytest.raises(ValueError, match="open device session"):
            coordinator.finish(ticket)
        assert coordinator.snapshot()["busy"]
    coordinator.finish(ticket)


def test_import_admission_is_atomic():
    coordinator = OperationCoordinator()

    def attempt(_):
        try:
            return coordinator.admit("import")
        except BusyError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [ticket for ticket in pool.map(attempt, range(16)) if ticket]
    assert len(admitted) == 1
    coordinator.finish(admitted[0])


def test_player_coordinator_does_not_import_device_modules():
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = "import sys; from operations import OperationCoordinator; c=OperationCoordinator(); t=c.admit('import'); c.begin_drain(); c.finish(t); assert c.wait_drained(0); assert 'device' not in sys.modules; assert 'jsymphonic' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent,
                            capture_output=True, text=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr


def test_finished_ticket_cannot_open_device_session(tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    coordinator = OperationCoordinator()
    ticket = coordinator.admit("list", device=device.capture_device_identity(tmp_path))
    coordinator.finish(ticket)
    with pytest.raises(ValueError):
        with coordinator.device_session(ticket):
            pass
