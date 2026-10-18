"""Packaged engine/API contract on a new disposable empty mount only."""
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import device
import jsymphonic
from jobs import JobStatus
from main import create_app
from test_api import ORIGIN, TOKEN, Scanner


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='opt-in real Java recovery contract')
def test_real_engine_recovery_inspection_acknowledgement_and_empty_repair(tmp_path):
    mount = tmp_path / 'disposable-walkman'
    (mount / 'OMGAUDIO').mkdir(parents=True)
    volume = device.capture_device_identity(mount)
    api = SimpleNamespace(find_walkman=lambda: mount, capture_device_identity=device.capture_device_identity,
        device_info=device.device_info, backup_device=device.backup_device,
        **{name: getattr(jsymphonic, name) for name in ('list_tracks', 'list_playlists', 'add_tracks', 'remove_track',
           'inspect_playlist_journal', 'recover_playlist_journal', 'repair_playlists', 'JSymphonicError')})
    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN, scanner=Scanner(), device_api=api)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        unknown = app.state.jobs.create('interrupted-fixture', 1, kind='delete')
        unknown.needs_reconcile = True
        unknown.set_files([{'file_id': 'interrupted-fixture', 'volume_id': volume.volume_id, 'state': 'unknown'}])
        unknown.set_status(JobStatus.FAILED, 'Interrupted fixture deletion')
        inspected = client.get('/api/device/playlist-recovery/inspect')
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()['guarded'] is True
        assert len(inspected.json()['library_fingerprint']) == 64
        recovered = client.post('/api/device/playlist-recovery/recover', headers={'If-Match': inspected.headers['etag']})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()['outcome'] == 'none'
        repaired = client.post('/api/device/playlist-recovery/repair', headers={'If-Match': inspected.headers['etag']})
        assert repaired.status_code == 409, repaired.text
        assert repaired.json()['detail']['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'
        snapshot = client.get('/api/device/reconciliation')
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()['can_acknowledge'] is True
        acknowledged = client.post('/api/device/reconciliation', headers={'If-Match': snapshot.headers['etag']},
                                   json={'job_id': unknown.job_id, 'acknowledge': True})
        assert acknowledged.status_code == 200, acknowledged.text
        assert not unknown.needs_reconcile
        assert unknown.files[0]['state'] == 'unknown'
        assert not list(mount.rglob('*.*')), 'Empty inspection/recovery must not create device data'
