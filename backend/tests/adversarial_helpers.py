"""Deterministic fakes for offline adversarial backend tests.

These doubles hash real bytes and do not bypass production clearance checks.
Nothing in this module opens a network connection.
"""
from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import device
import jsymphonic

TOKEN = 'adversarial-test-token'
ORIGIN = 'http://127.0.0.1:8765'

RESERVED_STEMS = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{n}' for n in range(1, 10)),
    *(f'LPT{n}' for n in range(1, 10)),
}


class HashScanner:
    """Clearance double that still binds the verdict to the current bytes."""

    def __init__(self):
        self.ok = True
        self.calls = []

    def scan_audio(self, path):
        data = path.read_bytes()
        result = dict(
            ok=self.ok,
            state='CLEAN' if self.ok else 'ERROR',
            reason='Clean' if self.ok else 'Defender unavailable',
            reason_code='clean' if self.ok else 'defender_unavailable',
            defender_status='clean' if self.ok else 'unavailable',
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        self.calls.append(path)
        return SimpleNamespace(to_dict=lambda result=result: dict(result))

    def scan_artwork(self, path):
        return self.scan_audio(path)

    def clearance_valid(self, path, record, trusted_status=None):
        if not self.ok or not record or not record.get('ok'):
            return False
        try:
            size = path.stat().st_size
        except OSError:
            return False
        return (record.get('sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
                and int(record.get('size_bytes', -1)) == size)

    def snapshot(self):
        return {'rows': [], 'engine': 'adversarial hash scanner'}


class ClaimedSize(io.BytesIO):
    """Seek/tell report `claimed` bytes; read() still returns the real buffer."""

    def __init__(self, data: bytes, claimed: int):
        super().__init__(data)
        self._claimed = claimed
        self._sizing = False

    def seek(self, pos, whence=0):
        if whence == 2:
            self._sizing = True
            return self._claimed
        self._sizing = False
        return super().seek(pos, whence)

    def tell(self):
        if self._sizing:
            return self._claimed
        return super().tell()


class GrowingStream:
    """Size probe sees `declared`; the following read appends `extra`."""

    def __init__(self, declared: bytes, extra: bytes):
        self._blob = declared + extra
        self._declared = len(declared)
        self._pos = 0
        self._sizing = False

    def seek(self, pos, whence=0):
        if whence == 2:
            self._sizing = True
            self._pos = self._declared
            return self._pos
        self._sizing = False
        self._pos = pos
        return self._pos

    def tell(self):
        return self._pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._blob) - self._pos
        chunk = self._blob[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class DisconnectingStream(io.BytesIO):
    def read(self, n=-1):
        raise ConnectionError('connection reset by peer')


def fake_device(volume, list_tracks, add_tracks, remove_track=None):
    def remove(mount, track_id):
        if remove_track:
            remove_track(mount, track_id)

    return SimpleNamespace(
        find_walkman=lambda: volume,
        capture_device_identity=device.capture_device_identity,
        device_info=device.device_info,
        backup_device=device.backup_device,
        list_tracks=list_tracks,
        remove_track=remove,
        add_tracks=add_tracks,
        JSymphonicError=jsymphonic.JSymphonicError,
    )


def assert_store_contained(store):
    """Every managed file is a source artifact under its own media-id directory."""
    from pathlib import Path
    from media_store import MIMES

    allowed = {'.bin', *MIMES}
    files = [path for path in store.root.rglob('*') if path.is_file()]
    for path in files:
        assert path.resolve().is_relative_to(store.root.resolve()), path
        relative = path.relative_to(store.root)
        assert len(relative.parts) == 2, relative
        folder, name = relative.parts
        assert len(folder) == 32 and all(char in '0123456789abcdef' for char in folder), folder
        assert name.startswith('source'), name
        suffix = Path(name).suffix
        assert suffix in allowed, name
        assert Path(name).stem.upper() not in RESERVED_STEMS, name
        assert not name.startswith('.'), name
        assert '\x00' not in name and ':' not in name and '\\' not in name and '/' not in name
    return files
