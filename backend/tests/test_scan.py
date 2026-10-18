"""Strict scanner contract tests. Fixtures are harmless synthetic audio bytes."""
from __future__ import annotations

import hashlib
import os
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
        (SimpleNamespace(returncode=2, stdout="Scanning C:\\x\\source.mp3 found 1 threats.", stderr=""), "BLOCKED", "defender_threat", "threat"),
        (SimpleNamespace(returncode=0, stdout="Scanning C:\\x\\source.mp3 found 3 threats.", stderr=""), "BLOCKED", "defender_threat", "threat"),
        # MpCmdRun exit code 2 also covers scanning errors; without a detection line it is a non-clearing error.
        (SimpleNamespace(returncode=2, stdout="Scan starting...\nCmdTool: Failed with hr = 0x80070002", stderr=""), "ERROR", "defender_scan_error", "error"),
        # Access denied arrives with exit code 2 as well and must still request elevation.
        (SimpleNamespace(returncode=2, stdout="Scan starting...\nCmdTool: Failed with hr = 0x80070005. Check C:\\MpCmdRun.log", stderr=""), "AWAITING_PERMISSION", "defender_elevation_required", "elevation_required"),
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
    (SimpleNamespace(returncode=2, stdout='found 1 threats.', stderr=''), 'BLOCKED'),
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


CLEAN_OUTPUT_WITH_THREAT_IN_PATH = (
    "Scan starting...\nScan finished.\n"
    "Scanning C:\\Users\\Threat Lab\\AppData\\Local\\Walkman Bridge\\cache\\"
    "0123456789abcdef0123456789abcdef\\source.mp3 found no threats.\n"
)


def test_clean_scan_of_a_path_naming_threats_stays_clean(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_invoke_defender",
                        lambda _path: SimpleNamespace(returncode=0, stdout=CLEAN_OUTPUT_WITH_THREAT_IN_PATH, stderr=""))
    result = scan.scan_audio(path)
    assert result.ok is True
    assert result.state == "CLEAN"
    assert scan.snapshot()["threats"] == 0


def test_exit_code_two_without_a_detection_line_is_an_error_not_a_threat(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_invoke_defender",
                        lambda _path: SimpleNamespace(returncode=2, stdout="Scan starting...\nCmdTool: Failed with hr = 0x8050800C", stderr=""))
    result = scan.scan_audio(path)
    assert result.ok is False
    assert (result.state, result.reason_code, result.defender_status) == ("ERROR", "defender_scan_error", "error")
    assert "exit code 2" in result.reason
    snapshot = scan.snapshot()
    assert snapshot["threats"] == 0 and snapshot["blocked"] == 0 and snapshot["cleared"] == 0


def test_detection_line_counts_as_a_threat_in_the_snapshot(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    monkeypatch.setattr(scan, "_invoke_defender",
                        lambda _path: SimpleNamespace(returncode=2, stdout="Scan finished.\nScanning C:\\x\\source.mp3 found 1 threats.", stderr=""))
    result = scan.scan_audio(path)
    assert (result.state, result.reason_code, result.defender_status) == ("BLOCKED", "defender_threat", "threat")
    snapshot = scan.snapshot()
    assert snapshot["threats"] == 1 and snapshot["blocked"] == 1 and snapshot["cleared"] == 0


def _id3v23_tag(frames: bytes, padding: bytes = b"") -> bytes:
    n = len(frames) + len(padding)
    size = bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    return b"ID3\x03\x00\x00" + size + frames + padding


def _minimal_frame(frame_id: bytes = b"TXXX", payload: bytes = b"\x00") -> bytes:
    return frame_id + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def test_frame_walk_is_linear_for_a_tag_packed_with_tiny_frames(tmp_path: Path):
    import time
    frame = _minimal_frame()
    count = (scan.MAX_TAG_BYTES - 1024) // len(frame)
    path = tmp_path / "packed.mp3"
    path.write_bytes(_id3v23_tag(frame * count) + MPEG_FRAME)
    started = time.perf_counter()
    result = scan.scan_audio(path)
    elapsed = time.perf_counter() - started
    assert result.ok is True, result.reason
    # The former per-frame body re-slicing took over ten seconds for this input.
    assert elapsed < 5.0, f"ID3 frame walk took {elapsed:.1f}s for {count} frames"


@pytest.mark.parametrize("frames, padding, malformed", [
    (_minimal_frame() * 3, b"\x00" * 64, False),            # padding after the last frame is valid
    (_minimal_frame() * 3, b"\x00" * 8 + b"\x01", True),    # non-zero byte inside padding
    (_minimal_frame()[:-1], b"", True),                      # truncated final frame
    (_minimal_frame(b"tx1x"), b"", True),                    # lowercase frame id
    (_minimal_frame(b"TXXX", b""), b"", True),               # zero-length frame
    # Frames of differing sizes with APIC second: sizes and payloads must be read at the cursor.
    (_minimal_frame(b"TIT2", b"\x00abc") + _minimal_frame(b"APIC", b"\x00image/jpeg\x00\x03\x00\xff\xd8\xff\xd9") + _minimal_frame(), b"", False),
    (_minimal_frame(b"TIT2", b"\x00abc") + _minimal_frame(b"APIC", b"\x00image/jpeg-without-terminator") + _minimal_frame(), b"", True),
])
def test_frame_walk_preserves_padding_and_malformation_rules(tmp_path: Path, frames, padding, malformed):
    path = tmp_path / "frames.mp3"
    path.write_bytes(_id3v23_tag(frames, padding) + MPEG_FRAME)
    result = scan.scan_audio(path)
    assert result.ok is (not malformed)
    if malformed:
        assert result.reason_code == "invalid_audio_format"


def test_frame_walk_handles_v22_and_v24_headers(tmp_path: Path):
    # Two frames of differing sizes per version, so the second header must be read at the cursor.
    v22_frames = b"TT2" + (5).to_bytes(3, "big") + b"\x00abcd" + b"TP1" + (1).to_bytes(3, "big") + b"\x00"
    v22 = b"ID3\x02\x00\x00" + bytes((0, 0, 0, len(v22_frames))) + v22_frames + MPEG_FRAME
    v24_frames = b"TIT2" + bytes((0, 0, 0, 5)) + b"\x00\x00" + b"\x00abcd" + b"TPE1" + bytes((0, 0, 0, 1)) + b"\x00\x00" + b"\x00"
    v24 = b"ID3\x04\x00\x00" + bytes((0, 0, 0, len(v24_frames))) + v24_frames + MPEG_FRAME
    for content in (v22, v24):
        path = tmp_path / "versions.mp3"
        path.write_bytes(content)
        assert scan.scan_audio(path).ok is True


def test_frame_walk_starts_after_an_extended_header(tmp_path: Path):
    frames = _minimal_frame(b"TIT2", b"\x00abc") + _minimal_frame()
    extended = (6).to_bytes(4, "big") + b"\x00\x00" + b"\x00\x00\x00\x00"  # v2.3: size excludes its own 4 bytes
    n = len(extended) + len(frames)
    size = bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    path = tmp_path / "extended.mp3"
    path.write_bytes(b"ID3\x03\x00\x40" + size + extended + frames + MPEG_FRAME)
    assert scan.scan_audio(path).ok is True
    oversized = (200).to_bytes(4, "big") + b"\x00\x00"
    n = len(oversized)
    size = bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    path.write_bytes(b"ID3\x03\x00\x40" + size + oversized + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"


def test_extended_header_size_must_read_alike_for_the_specification_and_ffmpeg(tmp_path: Path):
    """ffmpeg reads the ID3v2.3 extended-header size with 7 bits per byte; the specification uses 32."""
    def synchsafe(n):
        return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    hidden = _minimal_frame(b"APIC", b"\x00image/jpeg-without-terminator") + _minimal_frame(b"TIT2", b"\x00HIDDEN")
    visible = _minimal_frame(b"TALB", b"\x00visible")
    path = tmp_path / "extended.mp3"
    for size_bytes, gate_skip, ffmpeg_skip in ((b"\x00\x00\x01\x00", 260, 132), (b"\x00\x00\x00\x8a", 142, 14)):
        # The gate used to start at gate_skip while ffmpeg starts at ffmpeg_skip: frames in between
        # were parsed by ffmpeg and never validated. Both such tags are now rejected outright.
        gap = b"\x00" * (ffmpeg_skip - 4) + hidden
        gap += b"\x00" * (gate_skip - 4 - len(gap))
        body = size_bytes + gap + visible + b"\x00" * 8
        path.write_bytes(b"ID3\x03\x00\x40" + synchsafe(len(body)) + body + MPEG_FRAME)
        assert scan.scan_audio(path).reason_code == "invalid_audio_format", size_bytes.hex()
    # The two legitimate sizes read alike and are accepted.
    for extended in ((6).to_bytes(4, "big") + b"\x00" * 6, (10).to_bytes(4, "big") + b"\x00" * 10):
        body = extended + visible
        path.write_bytes(b"ID3\x03\x00\x40" + synchsafe(len(body)) + body + MPEG_FRAME)
        assert scan.scan_audio(path).ok is True
    # ID3v2.2 has no extended header (bit 6 is compression); ffmpeg does not skip anything there.
    v22 = b"TT2" + (5).to_bytes(3, "big") + b"\x00abcd"
    path.write_bytes(b"ID3\x02\x00\x40" + synchsafe(len(v22)) + v22 + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"


def test_unsynchronised_id3v23_tag_is_accepted_only_when_ffmpeg_reads_the_same_frames(tmp_path: Path):
    def synchsafe(n):
        return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    path = tmp_path / "unsync.mp3"
    # Flag set, nothing to decode: both readings agree and the tag is accepted.
    plain = _minimal_frame(b"TXXX", b"\x00plain text") + _minimal_frame(b"TALB", b"\x00album")
    path.write_bytes(b"ID3\x03\x00\x80" + synchsafe(len(plain)) + plain + MPEG_FRAME)
    assert scan.scan_audio(path).ok is True
    # A payload that unsynchronisation actually changed: the specification reads one six-byte TXXX
    # frame, ffmpeg reads six raw bytes and then a misaligned frame. The readings disagree, so the
    # tag is rejected rather than letting ffmpeg parse frames the gate never validated.
    payload = b"\x00\xff\xe0abc"
    on_disk = _minimal_frame(b"TXXX", payload).replace(b"\xff\xe0", b"\xff\x00\xe0")
    path.write_bytes(b"ID3\x03\x00\x80" + synchsafe(len(on_disk)) + on_disk + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    # ffmpeg's reading would hide an APIC frame the specification reading shows: rejected.
    apic = _minimal_frame(b"APIC", b"\x00image/jpeg\x00\x03\x00\xff\xd8\xff\xd9")
    text = _minimal_frame(b"TXXX", b"\x00" + b"\xff" * 10)
    body = text.replace(b"\xff", b"\xff\x00") + apic
    path.write_bytes(b"ID3\x03\x00\x80" + synchsafe(len(body)) + body + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    # Both readings valid but with different contents (FF 00 41 read as "FF 41" by ffmpeg and as
    # "FF 41 00" by the specification): the frame lists must be compared by content, not only by id.
    body = b"TXXX" + (3).to_bytes(4, "big") + b"\x00\x00" + b"\xff\x00\x41" + b"\x00" * 16
    path.write_bytes(b"ID3\x03\x00\x80" + synchsafe(len(body)) + body + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    # A two-byte payload FF 00: ffmpeg decodes it to one byte, the specification keeps two.
    body = b"TXXX" + (2).to_bytes(4, "big") + b"\x00\x00" + b"\xff\x00" + b"\x00" * 16
    path.write_bytes(b"ID3\x03\x00\x80" + synchsafe(len(body)) + body + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    # An unsynchronised byte inside a frame header (size 255 written as FF 00): the specification
    # decodes the header, ffmpeg reads it raw, so the readings disagree and the tag is rejected.
    body = b"TXXX" + b"\x00\x00\x00\xff\x00" + b"\x00\x00" + b"\x41" * 255 + b"\x00" * 16
    path.write_bytes(b"ID3\x03\x00\x80" + synchsafe(len(body)) + body + MPEG_FRAME)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"


def test_unsynchronisation_decode_applies_only_to_v22_and_v23_with_the_flag(tmp_path: Path):
    def synchsafe(n):
        return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    # ID3v2.4 frame sizes count the on-disk (unsynchronised) bytes; the tag must not be decoded first.
    v24_payload = b"\x00\xff\x00\xe0abc"
    v24_frame = b"TXXX" + synchsafe(len(v24_payload)) + b"\x00\x00" + v24_payload
    path = tmp_path / "v24.mp3"
    path.write_bytes(b"ID3\x04\x00\x80" + synchsafe(len(v24_frame)) + v24_frame + MPEG_FRAME)
    assert scan.scan_audio(path).ok is True
    # ID3v2.3 with only the extended-header bit set: 0xFF 0x00 inside a payload is literal, not unsync.
    payload = b"\x00\xff\x00abc"
    frames = _minimal_frame(b"TXXX", payload)
    extended = (6).to_bytes(4, "big") + b"\x00\x00" + b"\x00\x00\x00\x00"
    body = extended + frames
    path.write_bytes(b"ID3\x03\x00\x40" + synchsafe(len(body)) + body + MPEG_FRAME)
    assert scan.scan_audio(path).ok is True


def test_every_consecutive_leading_id3_tag_is_validated(tmp_path: Path):
    first = _id3v23_tag(_minimal_frame(b"TIT2", b"\x00first"))
    good_second = _id3v23_tag(_minimal_frame(b"TALB", b"\x00second"))
    good_third = _id3v23_tag(_minimal_frame(b"TPE1", b"\x00the third one"))
    bad = _id3v23_tag(_minimal_frame(b"APIC", b"\x00image/jpeg-without-terminator") + _minimal_frame(b"TALB", b"\x00x"))
    assert len({len(first), len(good_second), len(good_third), len(bad)}) == 4  # distinct sizes pin the offsets
    path = tmp_path / "tags.mp3"
    path.write_bytes(first + good_second + good_third + _mpeg_frames(2))
    assert scan.scan_audio(path).ok is True
    path.write_bytes(first + bad + _mpeg_frames(2))
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    path.write_bytes(first + good_second + bad + _mpeg_frames(2))  # only the third tag is malformed
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    path.write_bytes(first * (scan.MAX_ID3_TAGS + 1) + _mpeg_frames(2))
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"


def test_footer_flag_is_honoured_only_for_id3v24(tmp_path: Path):
    """ffmpeg applies the 10-byte footer only to ID3v2.4; a v2.3 header with bit 0x10 must not hide the next tag."""
    def synchsafe(n):
        return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))
    first_body = _minimal_frame(b"TIT2", b"\x00first")
    flagged_v23 = b"ID3\x03\x00\x10" + synchsafe(len(first_body)) + first_body
    hidden = _id3v23_tag(_minimal_frame(b"APIC", b"\x00image/jpeg-without-terminator") + _minimal_frame(b"TALB", b"\x00hidden"))
    path = tmp_path / "footer.mp3"
    path.write_bytes(flagged_v23 + hidden + _mpeg_frames(2))
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    # A real ID3v2.4 footer is stepped over, and the good tag that follows it is validated and accepted.
    v24_body = b"TIT2" + synchsafe(6) + b"\x00\x00" + b"\x00first"
    v24 = b"ID3\x04\x00\x10" + synchsafe(len(v24_body)) + v24_body + b"3DI\x04\x00\x10" + synchsafe(len(v24_body))
    good_second = _id3v23_tag(_minimal_frame(b"TALB", b"\x00second"))
    path.write_bytes(v24 + good_second + _mpeg_frames(2))
    assert scan.scan_audio(path).ok is True
    path.write_bytes(v24 + hidden + _mpeg_frames(2))
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    # Positive cases pin the rule at both sites: a v2.3 header with bit 0x10 is not shortened by ten
    # bytes, so audio or a good tag directly after it is read where ffmpeg reads it.
    path.write_bytes(flagged_v23 + _mpeg_frames(2))
    assert scan.scan_audio(path).ok is True
    path.write_bytes(flagged_v23 + good_second + _mpeg_frames(2))
    assert scan.scan_audio(path).ok is True


def _mpeg_frames(count: int) -> bytes:
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz: 417-byte frames.
    return (b"\xff\xfb\x90\x00" + b"\x00" * 413) * count


@pytest.mark.parametrize("prefix", [ID3_EMPTY, b""])
@pytest.mark.parametrize("filler", [b"\x00" * 2048, b"APETAGEX" + b"\x11" * 300, b"\x7f" * 60000])
def test_padding_or_junk_before_the_first_frame_is_tolerated(tmp_path: Path, prefix, filler):
    path = tmp_path / "padded.mp3"
    path.write_bytes(prefix + filler + _mpeg_frames(2))
    assert scan.scan_audio(path).ok is True


def test_a_lone_sync_word_inside_junk_is_not_audio(tmp_path: Path):
    path = tmp_path / "fake.mp3"
    path.write_bytes(ID3_EMPTY + b"\x00" * 64 + b"\xff\xfb\x90\x00" + b"\x00" * 100)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"
    path.write_bytes(ID3_EMPTY + b"\x00" * (scan.SYNC_SEARCH_BYTES + 16) + _mpeg_frames(2))
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"  # beyond the bounded window


def test_adts_aac_with_leading_padding_is_tolerated(tmp_path: Path):
    frame = bytes((0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC)) + b"\x00" * 9  # 16-byte ADTS frame
    path = tmp_path / "padded.aac"
    path.write_bytes(b"\x00" * 512 + frame * 3)
    assert scan.scan_audio(path).ok is True
    path.write_bytes(b"\x00" * 512 + frame)
    assert scan.scan_audio(path).reason_code == "invalid_audio_format"


def test_frame_length_tables_match_known_headers():
    assert scan._mpeg_frame_length(b"\xff\xfb\x90\x00") == 417      # MPEG-1 L3 128k 44.1k
    assert scan._mpeg_frame_length(b"\xff\xfb\x92\x00") == 418      # same frame with the padding bit
    assert scan._mpeg_frame_length(b"\xff\xfb\x94\x00") == 384      # MPEG-1 L3 128k 48k
    assert scan._mpeg_frame_length(b"\xff\xfb\xe0\x00") == 1044     # MPEG-1 L3 320k 44.1k
    assert scan._mpeg_frame_length(b"\xff\xe3\x40\x00") == 208      # MPEG-2.5 L3 32k 11.025k: 72*32000//11025
    assert scan._mpeg_frame_length(b"\xff\xf3\x90\x00") == 261      # MPEG-2 L3 80k 22.05k: 72*80000//22050
    assert scan._mpeg_frame_length(b"\xff\xfd\x90\x00") == 522      # MPEG-1 L2 160k 44.1k: 144*160000//44100
    assert scan._mpeg_frame_length(b"\xff\xff\x90\x00") == 312      # MPEG-1 L1 288k 44.1k: (12*288000//44100)*4
    assert scan._mpeg_frame_length(b"\xff\xfb\x00\x00") is None     # free-format bitrate index
    assert scan._mpeg_frame_length(b"\xff\xfb\xf0\x00") is None     # reserved bitrate index
    assert scan._mpeg_frame_length(b"\xff\xfb\x9c\x00") is None     # reserved sample-rate index
    assert scan._mpeg_frame_length(b"\xff\xeb\x90\x00") is None     # reserved version bits
    assert scan._adts_frame_length(bytes((0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC))) == 16
    assert scan._adts_frame_length(bytes((0xFF, 0xF1, 0x50, 0x80, 0x00, 0x1F, 0xFC))) is None  # shorter than its header


# Independent reference for ISO 11172-3 / 13818-3 frame lengths, written out in full.
_REFERENCE_BITRATES = {
    ("1", 1): [32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    ("1", 2): [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    ("1", 3): [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    ("2", 1): [32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    ("2", 2): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
    ("2", 3): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_REFERENCE_RATES = {"1": [44100, 48000, 32000], "2": [22050, 24000, 16000], "2.5": [11025, 12000, 8000]}


def test_frame_length_matches_the_reference_formula_for_every_valid_header():
    checked = 0
    for version_bits, version in ((3, "1"), (2, "2"), (0, "2.5")):
        for layer_bits, layer in ((3, 1), (2, 2), (1, 3)):
            for bitrate_index in range(1, 15):
                for rate_index in range(3):
                    for padding in (0, 1):
                        header = bytes((0xFF, 0xE0 | (version_bits << 3) | (layer_bits << 1) | 1,
                                        (bitrate_index << 4) | (rate_index << 2) | (padding << 1), 0x00))
                        generation = "1" if version == "1" else "2"
                        bitrate = _REFERENCE_BITRATES[(generation, layer)][bitrate_index - 1] * 1000
                        rate = _REFERENCE_RATES[version][rate_index]
                        if layer == 1:
                            expected = (12 * bitrate // rate + padding) * 4
                        elif layer == 3 and version != "1":
                            expected = 72 * bitrate // rate + padding
                        else:
                            expected = 144 * bitrate // rate + padding
                        assert scan._mpeg_frame_length(header) == expected, header.hex()
                        checked += 1
    assert checked == 3 * 3 * 14 * 3 * 2


def test_opus_extension_is_an_ogg_container(tmp_path: Path):
    path = tmp_path / "song.opus"
    path.write_bytes(b"OggS" + b"\x00" * 60)
    assert scan.scan_audio(path).ok is True


def test_platform_fallback_prefers_the_numerically_newest_defender(monkeypatch, tmp_path: Path):
    program_files = tmp_path / "Program Files"
    program_files.mkdir()
    platform = tmp_path / "ProgramData" / "Microsoft" / "Windows Defender" / "Platform"
    for folder in ("4.18.25080.5-0", "4.18.25080.11-0", "4.18.24100.4-0"):
        (platform / folder).mkdir(parents=True)
        (platform / folder / "MpCmdRun.exe").write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    monkeypatch.setattr(scan.shutil, "which", lambda name: None)
    assert scan._defender_exe() == platform / "4.18.25080.11-0" / "MpCmdRun.exe"


def test_consumption_checks_rehash_every_time_and_reuse_status_briefly(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    record = scan.scan_audio(path).to_dict()
    hashed, queried = [], []
    original = scan._fingerprint
    monkeypatch.setattr(scan, "_fingerprint", lambda target: hashed.append(target) or original(target))
    monkeypatch.setattr(scan, "_query_defender_status", lambda: queried.append(1) or dict(STATUS))
    assert scan.clearance_valid(path, record) is True
    assert scan.clearance_valid(path, record) is True
    assert (len(hashed), len(queried)) == (2, 1)
    # A same-size in-place rewrite with the mtime restored is still caught: the digest is never cached.
    before = path.stat()
    path.write_bytes(_mp3_stub()[:-1] + b"\x01")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert scan.clearance_valid(path, record) is False
    assert len(hashed) == 3


def test_status_cache_expires_and_requeries_live(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    record = scan.scan_audio(path).to_dict()
    clock = [1000.0]
    monkeypatch.setattr(scan, "_monotonic", lambda: clock[0])
    assert scan.clearance_valid(path, record) is True  # fills the cache
    monkeypatch.setattr(scan, "_query_defender_status", lambda: {"available": False})
    clock[0] += scan.STATUS_CACHE_SECONDS  # the documented reuse window
    assert scan.clearance_valid(path, record) is True
    clock[0] += 0.01  # expired: the live query now says unavailable
    assert scan.clearance_valid(path, record) is False
    monkeypatch.setattr(scan, "_query_defender_status", lambda: dict(STATUS))
    assert scan.clearance_valid(path, record) is True


def test_scans_never_use_cached_status_and_unavailable_status_is_not_cached(monkeypatch, tmp_path: Path):
    path = tmp_path / "song.mp3"
    path.write_bytes(_mp3_stub())
    record = scan.scan_audio(path).to_dict()
    assert scan.clearance_valid(path, record) is True  # populates the status cache
    monkeypatch.setattr(scan, "_query_defender_status", lambda: {"available": False})
    assert scan.scan_audio(path).reason_code == "defender_status_unavailable"
    scan.reset_for_tests()
    assert scan.clearance_valid(path, record) is False
    monkeypatch.setattr(scan, "_query_defender_status", lambda: dict(STATUS))
    assert scan.clearance_valid(path, record) is True  # recovery is immediate
