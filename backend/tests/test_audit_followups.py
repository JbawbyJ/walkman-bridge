"""Regression tests for low-severity audit findings confirmed by independent verification."""
from __future__ import annotations

import ctypes
import hashlib
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adversarial_helpers import ORIGIN, TOKEN, HashScanner, fake_device
import application
import device
import scan
from jobs import JobStatus, JobStore
from scan_bridge import ElevationBridge


def _close(app):
    app.state.jobs.close()
    app.state.store._db.close()


def test_validation_errors_use_the_documented_detail_shape(tmp_path):
    app = application.create_app(product='player', data_dir=tmp_path / 'state', token=TOKEN,
                                 origin=ORIGIN, scanner=HashScanner())
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.patch('/api/preferences', json={'theme': 'secret-echo-value'})
        assert response.status_code == 422
        detail = response.json()['detail']
        assert detail['code'] == 'invalid_request'
        assert detail['message'].startswith('theme: ')
        # The rejected input is not echoed back to the renderer.
        assert 'secret-echo-value' not in response.text
        bad = client.patch('/api/playback-state', json={'position_seconds': -1})
        assert bad.status_code == 422
        assert bad.json()['detail']['message'].startswith('position_seconds: ')


def test_interrupted_legacy_migration_is_retried_on_the_next_start(tmp_path, monkeypatch):
    backend = tmp_path / 'backend'
    backend.mkdir()
    legacy = JobStore(backend / 'walkman-jobs.sqlite')
    legacy.create('legacy', 1, kind='transfer').set_status(JobStatus.DONE, 'Completed 1 of 1 files')
    legacy.close()
    monkeypatch.setattr(application, '__file__', str(backend / 'application.py'))
    data_dir = tmp_path / 'state'
    real_connect = sqlite3.connect

    class FailingSource:
        """The legacy connection whose copy dies after writing part of the destination."""
        def __init__(self, conn):
            self._conn = conn
        def backup(self, target, **_):
            target.execute('CREATE TABLE half_copied (x)')
            target.commit()
            raise sqlite3.OperationalError('disk I/O error')
        def __getattr__(self, name):
            return getattr(self._conn, name)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

    def connect(path, *args, **kwargs):
        conn = real_connect(path, *args, **kwargs)
        return FailingSource(conn) if str(path).endswith('walkman-jobs.sqlite') else conn

    monkeypatch.setattr(sqlite3, 'connect', connect)
    with pytest.raises(sqlite3.OperationalError):
        application.create_app(product='bridge', data_dir=data_dir, token=TOKEN, origin=ORIGIN,
                               scanner=HashScanner(), device_api=fake_device(tmp_path, lambda m: [], lambda *a: None))
    monkeypatch.setattr(sqlite3, 'connect', real_connect)
    assert not (data_dir / 'nightops.sqlite').exists()
    app = application.create_app(product='bridge', data_dir=data_dir, token=TOKEN, origin=ORIGIN,
                                 scanner=HashScanner(), device_api=fake_device(tmp_path, lambda m: [], lambda *a: None))
    try:
        migrated = app.state.jobs.get('legacy')
        assert migrated is not None and migrated.status == JobStatus.DONE
        assert not (data_dir / 'nightops.sqlite.migrating').exists()
    finally:
        _close(app)


def test_elevated_clearances_carry_the_hardened_policy_version(tmp_path):
    # Clearances issued before the ID3/format-gate hardening must not stay valid.
    assert scan.POLICY_VERSION != 'defender-strict-v1'
    media = tmp_path / 'cache' / 'song.mp3'
    media.parent.mkdir()
    media.write_bytes(b'harmless audio')
    bridge = ElevationBridge([media.parent], result_ttl_seconds=60)
    request = bridge.request('media-1', media, hashlib.sha256(media.read_bytes()).hexdigest(), media.stat().st_size)
    bridge.claim(request['request_id'], request['nonce'], helper_pid=4321)
    accepted = bridge.submit({
        **request, 'helper_pid': 4321, 'ok': True, 'state': 'CLEAN', 'reason': 'clean',
        'reason_code': 'clean', 'defender_status': 'clean', 'defender_engine_version': '1',
        'defender_signature_version': '1', 'defender_platform_version': '1', 'exit_code': 0, 'scanned_at': 1.0,
    })
    assert accepted['policy_version'] == scan.POLICY_VERSION
    failed = bridge.request('media-2', media, hashlib.sha256(media.read_bytes()).hexdigest(), media.stat().st_size)
    assert bridge.cancel(failed['request_id'], 'elevation_cancelled')['policy_version'] == scan.POLICY_VERSION


def _mount(tmp_path):
    mount = tmp_path / 'mount'
    (mount / 'OMGAUDIO').mkdir(parents=True)
    (mount / 'OMGAUDIO' / 'song.OMA').write_bytes(b'original')
    return mount


def test_failed_backup_removes_its_unpublished_copy(tmp_path, monkeypatch):
    mount, backups = _mount(tmp_path), tmp_path / 'backups'
    real_copytree = device.shutil.copytree

    def fail(src, dest, **kwargs):
        monkeypatch.setattr(device.shutil, 'copytree', real_copytree)  # copytree recurses via the module
        real_copytree(src, dest, **kwargs)
        raise OSError('disk full')

    monkeypatch.setattr(device.shutil, 'copytree', fail)
    with pytest.raises(device.BackupError, match='disk full'):
        device.backup_device(mount, backups)
    assert list(backups.iterdir()) == []


def test_unverified_backup_copy_is_removed(tmp_path, monkeypatch):
    mount, backups = _mount(tmp_path), tmp_path / 'backups'
    real_copytree = device.shutil.copytree

    def corrupt(src, dest, **kwargs):
        monkeypatch.setattr(device.shutil, 'copytree', real_copytree)  # copytree recurses via the module
        real_copytree(src, dest, **kwargs)
        (dest / 'OMGAUDIO' / 'song.OMA').write_bytes(b'tampered')

    monkeypatch.setattr(device.shutil, 'copytree', corrupt)
    with pytest.raises(device.BackupError, match='verif'):
        device.backup_device(mount, backups)
    assert list(backups.iterdir()) == []


def test_backup_sweeps_partials_left_by_a_crash_and_nothing_else(tmp_path):
    mount, backups = _mount(tmp_path), tmp_path / 'backups'
    stale = backups / ('.2026-01-01-000000-' + 'a' * 32 + '.partial')
    (stale / 'OMGAUDIO').mkdir(parents=True)
    locked = stale / 'OMGAUDIO' / 'song.OMA'
    locked.write_bytes(b'half')
    os.chmod(locked, stat.S_IREAD)
    published = backups / '2026-01-01-000000'
    published.mkdir()
    (published / 'kept').write_bytes(b'published backup')
    unrelated = backups / '.notes.partial'
    unrelated.mkdir()
    result = device.backup_device(mount, backups)
    assert not stale.exists()
    assert (published / 'kept').read_bytes() == b'published backup'
    assert unrelated.is_dir()
    assert (result / 'OMGAUDIO' / 'song.OMA').read_bytes() == b'original'


def test_windows_discovery_never_probes_network_optical_or_unassigned_letters(monkeypatch):
    kinds = {'C': 3, 'D': 2, 'E': 5, 'F': 6, 'Z': 4}
    kernel32 = SimpleNamespace(GetDriveTypeW=lambda root: kinds.get(root[0], 1))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel32), raising=False)
    monkeypatch.setattr(device.platform, 'system', lambda: 'Windows')
    probed = []
    monkeypatch.setattr(device.Path, 'exists', lambda self, **_: probed.append(str(self)) or True)
    candidates = device._candidates()
    monkeypatch.undo()
    assert candidates == [Path('D:\\'), Path('C:\\')]
    assert sorted(probed) == ['C:\\', 'D:\\']


def _bridge_with(tmp_path, **overrides):
    volume = tmp_path / 'device'
    (volume / 'OMGAUDIO').mkdir(parents=True)
    api = fake_device(volume, lambda m: [], lambda *a: None)
    for name, value in overrides.items():
        setattr(api, name, value)
    return application.create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN,
                                  origin=ORIGIN, scanner=HashScanner(), device_api=api)


def test_device_swapped_before_capture_is_a_structured_409(tmp_path):
    def unplugged(mount):
        raise device.DeviceChangedError('Walkman volume is no longer available')

    app = _bridge_with(tmp_path, capture_device_identity=unplugged)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        for response in (client.get('/api/tracks'), client.get('/api/device'), client.post('/api/backup')):
            assert response.status_code == 409, response.text
            assert response.json()['detail'] == 'Walkman volume is no longer available'
        assert client.get('/api/engine-busy').json()['busy'] is False


def test_device_unplugged_while_reading_info_is_a_structured_409(tmp_path):
    def vanished(mount):
        raise FileNotFoundError(2, 'No such file or directory', str(mount))

    app = _bridge_with(tmp_path, device_info=vanished)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.get('/api/device')
        assert response.status_code == 409, response.text
        assert 'disconnected' in response.json()['detail']
        assert client.get('/api/engine-busy').json()['busy'] is False

