from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from scan_bridge import BridgeError, ElevationBridge


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_result(request: dict, helper_pid: int = 4321) -> dict:
    return {
        **request, "helper_pid": helper_pid, "ok": True, "state": "CLEAN",
        "reason": "Microsoft Defender reported clean", "reason_code": "clean",
        "defender_status": "clean", "defender_engine_version": "1.1.2",
        "defender_signature_version": "1.2.3", "defender_platform_version": "4.5.6",
        "exit_code": 0, "scanned_at": 1234.0,
    }


def test_request_claim_result_binds_every_identity_and_waits(tmp_path: Path):
    media = tmp_path / "cache" / "song.mp3"
    media.parent.mkdir(); media.write_bytes(b"harmless audio")
    bridge = ElevationBridge([media.parent], result_ttl_seconds=60)
    request = bridge.request("media-1", media, _digest(media), media.stat().st_size)
    assert bridge.pending() == [request]
    bridge.claim(request["request_id"], request["nonce"], helper_pid=4321)
    accepted = bridge.submit(_clean_result(request))
    assert accepted["ok"] is True
    assert bridge.wait(request["request_id"], timeout=0.01) == accepted
    assert bridge.trusted_status(accepted) == {
        "available": True, "active": True, "real_time_protection": True,
        "running_mode": "Normal", "engine_version": "1.1.2",
        "signature_version": "1.2.3", "platform_version": "4.5.6",
    }
    with pytest.raises(BridgeError):
        bridge.trusted_status({**accepted, "sha256": "0" * 64})


@pytest.mark.parametrize("changed", [
    {"nonce": "wrong"}, {"media_id": "wrong"}, {"sha256": "0" * 64},
    {"size_bytes": 999}, {"helper_pid": 99}, {"exit_code": 7},
    {"defender_status": "unknown"},
])
def test_result_tampering_is_rejected(tmp_path: Path, changed: dict):
    media = tmp_path / "cache" / "song.mp3"
    media.parent.mkdir(); media.write_bytes(b"harmless audio")
    bridge = ElevationBridge([media.parent])
    request = bridge.request("media-1", media, _digest(media), media.stat().st_size)
    bridge.claim(request["request_id"], request["nonce"], helper_pid=4321)
    with pytest.raises(BridgeError):
        bridge.submit({**_clean_result(request), **changed})
    assert bridge.wait(request["request_id"], timeout=0) is None


def test_path_must_be_managed_and_cannot_be_symlink(tmp_path: Path):
    root = tmp_path / "cache"; root.mkdir()
    outside = tmp_path / "outside.mp3"; outside.write_bytes(b"audio")
    bridge = ElevationBridge([root])
    with pytest.raises(BridgeError, match="managed"):
        bridge.request("m", outside, _digest(outside), outside.stat().st_size)
    link = root / "link.mp3"
    try: link.symlink_to(outside)
    except OSError: pytest.skip("symlink creation unavailable")
    with pytest.raises(BridgeError, match="reparse"):
        bridge.request("m", link, _digest(outside), outside.stat().st_size)


def test_content_change_cancels_request_and_wait_unblocks(tmp_path: Path):
    media = tmp_path / "cache" / "song.mp3"
    media.parent.mkdir(); media.write_bytes(b"first")
    bridge = ElevationBridge([media.parent])
    request = bridge.request("media", media, _digest(media), media.stat().st_size)
    bridge.claim(request["request_id"], request["nonce"], 777)
    media.write_bytes(b"second")
    with pytest.raises(BridgeError, match="changed"):
        bridge.submit({**request, "helper_pid": 777, "ok": False})
    assert bridge.wait(request["request_id"], timeout=0)["reason_code"] == "content_changed"


def test_cancel_is_fail_closed_and_wakes_waiter(tmp_path: Path):
    media = tmp_path / "cache" / "song.mp3"
    media.parent.mkdir(); media.write_bytes(b"audio")
    bridge = ElevationBridge([media.parent])
    request = bridge.request("media", media, _digest(media), media.stat().st_size)
    seen = []
    waiter = threading.Thread(target=lambda: seen.append(bridge.wait(request["request_id"], 2)))
    waiter.start(); bridge.cancel(request["request_id"], "operator_cancelled"); waiter.join(1)
    assert seen[0]["ok"] is False
    assert seen[0]["state"] == "ERROR"
    assert seen[0]["reason_code"] == "operator_cancelled"


def test_authenticated_status_is_immediate_only(monkeypatch, tmp_path: Path):
    media = tmp_path / "cache" / "song.mp3"
    media.parent.mkdir(); media.write_bytes(b"audio")
    monkeypatch.setattr("scan_bridge.time.time", lambda: 100.0)
    bridge = ElevationBridge([media.parent], trusted_status_max_age_seconds=10)
    request = bridge.request("media", media, _digest(media), media.stat().st_size)
    bridge.claim(request["request_id"], request["nonce"], 4321)
    accepted = bridge.submit(_clean_result(request))
    assert bridge.trusted_status(accepted)["available"] is True
    monkeypatch.setattr("scan_bridge.time.time", lambda: 111.0)
    with pytest.raises(BridgeError, match="immediate"):
        bridge.trusted_status(accepted)


def test_pending_batch_is_published_together_and_cancelled_together(tmp_path):
    root = tmp_path / 'cache'; root.mkdir()
    bridge = ElevationBridge([root])
    requests = []
    for index in range(2):
        path = root / f'{index}.wav'; path.write_bytes(b'audio')
        requests.append(bridge.request(str(index), path, _digest(path), path.stat().st_size, batch_id='batch'))
        assert bridge.pending() == []
    bridge.release_batch('batch')
    assert len(bridge.pending()) == 2
    bridge.cancel_batch('batch')
    assert bridge.pending() == []
    assert all(bridge.wait(r['request_id'], timeout=0)['ok'] is False for r in requests)
