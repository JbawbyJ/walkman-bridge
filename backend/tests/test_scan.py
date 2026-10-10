"""Strict scanner contract tests. Fixtures are harmless synthetic audio bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import scan


MPEG_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 64
ID3_EMPTY = b"ID3\x03\x00\x00\x00\x00\x00\x00"
STATUS = {
    "available": True,
    "active": True,
    "real_time_protection": True,
    "running_mode": "Normal",
    "engine_version": "1.1.25000.2",
    "signature_version": "1.431.1.0",
    "platform_version": "4.18.25000.5",
}


def _mp3_stub() -> bytes:
    return ID3_EMPTY + MPEG_FRAME


def _id3v23_frame(frame_id: bytes, payload: bytes) -> bytes:
    frame = frame_id + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload
    n = len(frame)
    size = bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    return b"ID3\x03\x00\x00" + size + frame


def _apic(image: bytes) -> bytes:
    return _id3v23_frame(b"APIC", b"\x00image/jpeg\x00\x03\x00" + image) + MPEG_FRAME


@pytest.fixture(autouse=True)
def strict_clean_defender(monkeypatch):
    scan.reset_for_tests()
    monkeypatch.setattr(scan, "_query_defender_status", lambda: dict(STATUS))
    monkeypatch.setattr(
        scan,
        "_invoke_defender",
        lambda path: SimpleNamespace(returncode=0, stdout="Scan finished.", stderr=""),
    )
    yield
    scan.reset_for_tests()


def test_only_explicit_defender_clean_and_valid_format_passes(tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    result = scan.scan_audio(path)
    assert result.ok is True
    assert result.state == "CLEAN"
    assert result.reason_code == "clean"
    assert result.defender_status == "clean"
    assert result.sha256 == hashlib.sha256(_mp3_stub()).hexdigest()
    assert result.size_bytes == len(_mp3_stub())
    assert result.to_dict()["policy_version"] == scan.POLICY_VERSION


def test_defender_receives_absolute_path(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    seen = []
    monkeypatch.setattr(scan, "_invoke_defender", lambda target: seen.append(target) or SimpleNamespace(returncode=0, stdout="clean", stderr=""))
    scan.scan_audio(path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path)
    assert seen[0].is_absolute()


@pytest.mark.parametrize(
    ("outcome", "state", "code", "defender_status"),
    [
        (SimpleNamespace(returncode=2, stdout="Threat found", stderr=""), "BLOCKED", "defender_threat", "threat"),
        (SimpleNamespace(returncode=5, stdout="", stderr="Access is denied"), "AWAITING_PERMISSION", "defender_elevation_required", "elevation_required"),
        (SimpleNamespace(returncode=9, stdout="error", stderr=""), "ERROR", "defender_scan_error", "error"),
    ],
)
def test_defender_outcomes_fail_closed(monkeypatch, tmp_path: Path, outcome, state, code, defender_status):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_invoke_defender", lambda _path: outcome)
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.state == state
    assert result.reason_code == code
    assert result.defender_status == defender_status


def test_missing_defender_is_error_not_clear(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.flac"
    path.write_bytes(b"fLaC" + b"\x00" * 32)
    monkeypatch.setattr(scan, "_invoke_defender", lambda _path: None)
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.reason_code == "defender_unavailable"
    assert result.defender_status == "unavailable"


@pytest.mark.parametrize('outcome,expected', [
    (SimpleNamespace(returncode=0, stdout='Scan finished.', stderr=''), 'CLEAN'),
    (SimpleNamespace(returncode=2, stdout='Threat found', stderr=''), 'BLOCKED'),
    (SimpleNamespace(returncode=2, stdout='Scanning error', stderr=''), 'ERROR'),
    (None, 'ERROR'),
])
def test_generated_artwork_requires_its_own_explicit_defender_verdict(monkeypatch, tmp_path, outcome, expected):
    path = tmp_path / 'artwork.jpg'
    path.write_bytes(b'\xff\xd8\xff\xe0harmless fixture\xff\xd9')
    monkeypatch.setattr(scan, '_invoke_defender', lambda target: outcome)
    result = scan.scan_artwork(path)
    assert result.state == expected and result.ok == (expected == 'CLEAN')
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not scan.scan_audio(path).ok  # JPEG is not an accepted audio import.


def test_unavailable_signature_status_invalidates_explicit_zero_exit(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_query_defender_status", lambda: {"available": False})
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.reason_code == "defender_status_unavailable"


@pytest.mark.parametrize("disabled", [
    {"active": False},
    {"real_time_protection": False},
    {"running_mode": "Passive"},
])
def test_disabled_or_passive_defender_never_clears(monkeypatch, tmp_path: Path, disabled: dict):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_query_defender_status", lambda: {**STATUS, **disabled})
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.reason_code == "defender_status_unavailable"


def test_signature_status_permission_denial_requests_narrow_elevation(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_query_defender_status", lambda: {"available": False, "requires_elevation": True})
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.state == "AWAITING_PERMISSION"
    assert result.reason_code == "defender_elevation_required"


def test_valid_apic_treats_image_bytes_as_opaque(tmp_path: Path):
    path = tmp_path / "cover.mp3"
    path.write_bytes(_apic(b"\xff\xd8\xff\xe0harmless-MZ-PK\x03\x04-data\xff\xd9"))
    assert scan.scan_audio(path).ok is True


@pytest.mark.parametrize(
    "content",
    [
        b"ID3\x03\x00\x00\x00\x00\x01\x00" + b"APIC" + b"\x00" * 10,
        _id3v23_frame(b"APIC", b"\x00image/jpeg-without-terminator") + MPEG_FRAME,
        b"ID3\x03\x00\x00\x00\x00\x00\x0f" + b"APIC\x00\x00\x00\x40\x00\x00xxxxx",
    ],
)
def test_malformed_id3_or_apic_is_blocked(tmp_path: Path, content: bytes):
    path = tmp_path / "bad.mp3"
    path.write_bytes(content)
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.state == "BLOCKED"
    assert result.reason_code == "invalid_audio_format"


def test_file_changed_during_scan_fails_closed(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    def mutate(target: Path):
        target.write_bytes(_mp3_stub() + b"changed")
        return SimpleNamespace(returncode=0, stdout="clean", stderr="")
    monkeypatch.setattr(scan, "_invoke_defender", mutate)
    result = scan.scan_audio(path)
    assert result.ok is False
    assert result.reason_code == "content_changed"


def test_clearance_is_hash_size_policy_signature_and_24h_bound(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_utc_timestamp", lambda: 1_000_000.0)
    record = scan.scan_audio(path).to_dict()
    monkeypatch.setattr(scan, "_utc_timestamp", lambda: 1_086_399.0)
    assert scan.clearance_valid(path, record) is True
    monkeypatch.setattr(scan, "_utc_timestamp", lambda: 1_086_401.0)
    assert scan.clearance_valid(path, record) is False
    monkeypatch.setattr(scan, "_utc_timestamp", lambda: 1_000_000.0)
    assert scan.clearance_valid(path, {**record, "policy_version": "old"}) is False
    assert scan.clearance_valid(path, {**record, "defender_signature_version": "old"}) is False
    path.write_bytes(_mp3_stub() + b"tampered")
    assert scan.clearance_valid(path, record) is False


def test_authenticated_elevated_status_can_validate_when_unprivileged_status_is_denied(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_utc_timestamp", lambda: 1000.0)
    record = scan.scan_audio(path).to_dict()
    monkeypatch.setattr(scan, "_query_defender_status", lambda: {"available": False, "requires_elevation": True})
    assert scan.clearance_valid(path, record) is False
    assert scan.clearance_valid(path, record, trusted_status=STATUS) is True
    assert scan.clearance_valid(path, record, trusted_status={**STATUS, "signature_version": "stale"}) is False


def test_snapshot_reports_capability_and_rows(tmp_path: Path):
    path = tmp_path / "0123456789abcdef0123456789abcdef" / "song.mp3"
    path.parent.mkdir()
    path.write_bytes(_mp3_stub())
    scan.scan_audio(path)
    snapshot = scan.snapshot()
    assert snapshot["capability"]["available"] is True
    assert snapshot["cleared"] == 1
    assert snapshot["blocked"] == 0
    assert snapshot["rows"][0]["state"] == "CLEAN"
    assert snapshot["rows"][0]["reason_code"] == "clean"
    assert snapshot["rows"][0]["media_id"] == "0123456789abcdef0123456789abcdef"
