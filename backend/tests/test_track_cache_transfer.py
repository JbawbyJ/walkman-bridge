"""Device track cache and managed transfer artifacts while a long transfer is in flight."""
from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adversarial_helpers import ORIGIN, TOKEN, HashScanner, fake_device
from application import create_app
from jobs import JobStatus, JobStore
from media_service import MediaService
from media_store import MediaError, MediaStore
from operations import OperationCoordinator
import jsymphonic
import media_service
from transcode import AudioError


def _volume(tmp_path):
    volume = tmp_path / 'device'
    (volume / 'OMGAUDIO').mkdir(parents=True)
    return volume


def _complete(paths):
    return jsymphonic.AddResult(tuple(
        jsymphonic.FileOutcome(index, str(path), 'transferred', 'Device database update completed')
        for index, path in enumerate(paths)
    ))


def _import_ready(client, name='song.wav', payload=b'original-source'):
    response = client.post('/api/media/import', files={'files': (name, payload, 'audio/wav')})
    assert response.status_code == 200, response.text
    job = client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    return job['files'][0]['media_id']


def test_reads_during_conversion_serve_the_source_not_a_partial_derivative(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    entered, release = threading.Event(), threading.Event()

    def convert(src, out, kind, maximum, metadata=None):
        out.write_bytes(b'PARTIAL')
        entered.set()
        assert release.wait(10)
        out.write_bytes(b'COMPLETE-TRANSFER')
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    calls = {'list': 0}
    backing = [{'id': '1', 'title': 'Already there', 'artist': 'A', 'album': 'B'}]

    def list_tracks(mount):
        calls['list'] += 1
        return [dict(row) for row in backing]

    def add_tracks(mount, paths, callback):
        callback({'event': 'progress', 'step': 'transfer', 'percent': 50.0})
        backing.append({'id': '2', 'title': 'Just transferred', 'artist': 'A', 'album': 'B'})
        return _complete(paths)

    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=HashScanner(), device_api=fake_device(_volume(tmp_path), list_tracks, add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import_ready(client)
        assert [row['id'] for row in client.get('/api/tracks').json()] == ['1']
        assert calls['list'] == 1
        holder = {}

        def transfer():
            holder['response'] = client.post('/api/transfers', json={'media_ids': [media_id]})

        worker = threading.Thread(target=transfer)
        worker.start()
        try:
            assert entered.wait(10)
            store = app.state.store
            record = store.get(media_id)
            assert 'transfer' not in record['artifacts']
            assert client.get(f'/api/media/{media_id}/stream').content == b'original-source'
            cached = client.get('/api/tracks').json()
            assert [row['id'] for row in cached] == ['1']
            assert calls['list'] == 1
            assert all(row['title'] != 'Just transferred' for row in cached)
        finally:
            release.set()
            worker.join(10)
        assert not worker.is_alive()
        result = client.get('/api/jobs/' + holder['response'].json()['job_id']).json()
        assert result['status'] == 'done'
        assert result['files'][0]['state'] == 'transferred'
        record = app.state.store.get(media_id)
        path = app.state.store.path(record, 'transfer')
        assert path.read_bytes() == b'COMPLETE-TRANSFER'
        assert record['artifacts']['transfer']['sha256'] == hashlib.sha256(b'COMPLETE-TRANSFER').hexdigest()
        assert record['artifacts']['transfer']['size_bytes'] == len(b'COMPLETE-TRANSFER')
        refreshed = client.get('/api/tracks').json()
        assert [row['id'] for row in refreshed] == ['1', '2']
        assert calls['list'] == 2


def test_failed_transfer_invalidates_the_track_cache_and_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    monkeypatch.setattr(
        media_service, 'convert_managed',
        lambda src, out, kind, maximum, metadata=None: out.write_bytes(b'full-mp3') or out,
    )
    calls = {'list': 0}
    rows = [{'id': '1', 'title': 'Kept', 'artist': 'A', 'album': 'B'}]

    def list_tracks(mount):
        calls['list'] += 1
        return [dict(row) for row in rows]

    def add_tracks(mount, paths, callback):
        rows.append({'id': 'partial', 'title': 'INCOMPLETE', 'artist': '', 'album': ''})
        callback({'event': 'progress', 'step': 'transfer', 'percent': 20.0})
        raise jsymphonic.JSymphonicError('disconnected', needs_reconcile=True)

    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=HashScanner(), device_api=fake_device(_volume(tmp_path), list_tracks, add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import_ready(client)
        assert client.get('/api/tracks').json()[0]['id'] == '1'
        before = calls['list']
        response = client.post('/api/transfers', json={'media_ids': [media_id]})
        result = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert result['status'] != 'done'
        assert result['files'][0]['state'] == 'unknown'
        listed = client.get('/api/tracks').json()
        assert calls['list'] == before + 1
        assert [row['id'] for row in listed] == ['1', 'partial']
        record = app.state.store.get(media_id)
        path = app.state.store.path(record, 'transfer')
        assert path.read_bytes() == b'full-mp3'
        assert record['artifacts']['transfer']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_second_transfer_of_the_same_track_is_rejected_while_the_first_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    entered, release = threading.Event(), threading.Event()
    bodies = []

    def convert(src, out, kind, maximum, metadata=None):
        entered.set()
        assert release.wait(10)
        out.write_bytes(b'ONLY-COMPLETE')
        bodies.append(out.read_bytes() if False else b'ONLY-COMPLETE')
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    adds = []

    def add_tracks(mount, paths, callback):
        adds.append(tuple(Path(path).read_bytes() for path in paths))
        return _complete(paths)

    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=HashScanner(), device_api=fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import_ready(client, payload=b'source-bytes')
        holder = {}

        def transfer():
            holder['response'] = client.post('/api/transfers', json={'media_ids': [media_id]})

        worker = threading.Thread(target=transfer)
        worker.start()
        try:
            assert entered.wait(10)
            rejected = client.post('/api/transfers', json={'media_ids': [media_id]})
            assert rejected.status_code == 409, rejected.text
        finally:
            release.set()
            worker.join(10)
        result = client.get('/api/jobs/' + holder['response'].json()['job_id']).json()
        assert result['status'] == 'done'
        record = app.state.store.get(media_id)
        assert app.state.store.path(record, 'transfer').read_bytes() == b'ONLY-COMPLETE'
        assert adds == [(b'ONLY-COMPLETE',)]
        assert bodies == [b'ONLY-COMPLETE']


def test_drain_during_transfer_refuses_new_work_and_keeps_a_full_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    entered, release = threading.Event(), threading.Event()

    def convert(src, out, kind, maximum, metadata=None):
        out.write_bytes(b'PARTIAL')
        entered.set()
        assert release.wait(10)
        out.write_bytes(b'AFTER-DRAIN')
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)

    def add_tracks(mount, paths, callback):
        assert Path(paths[0]).read_bytes() == b'AFTER-DRAIN'
        return _complete(paths)

    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=HashScanner(), device_api=fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import_ready(client)
        holder = {}

        def transfer():
            holder['response'] = client.post('/api/transfers', json={'media_ids': [media_id]})

        worker = threading.Thread(target=transfer)
        worker.start()
        try:
            assert entered.wait(10)
            record = app.state.store.get(media_id)
            assert 'transfer' not in record['artifacts']
            assert client.get(f'/api/media/{media_id}/stream').content == b'original-source'
            assert client.post('/api/shutdown/drain', headers={'X-NightOps-Token': TOKEN}).status_code == 200
            assert client.post('/api/media/import', files={'files': ('other.wav', b'no', 'audio/wav')}).status_code == 409
        finally:
            release.set()
            worker.join(10)
        result = client.get('/api/jobs/' + holder['response'].json()['job_id']).json()
        assert result['status'] == 'done'
        path = app.state.store.path(app.state.store.get(media_id), 'transfer')
        assert path.read_bytes() == b'AFTER-DRAIN'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == app.state.store.get(media_id)['artifacts']['transfer']['sha256']


def test_quota_pressure_does_not_evict_or_publish_a_partial_transfer(tmp_path):
    root = tmp_path / 'cache'
    store = MediaStore(tmp_path / 'state.db', root, limit=100)
    record = store.import_file('kept.mp3', io.BytesIO(b'x' * 40))
    partial = root / record['id'] / 'transfer.mp3'
    partial.write_bytes(b'y' * 40)
    with pytest.raises(MediaError, match='full'):
        store.import_file('overflow.mp3', io.BytesIO(b'z' * 40))
    assert store.path(record).read_bytes() == b'x' * 40
    assert partial.read_bytes() == b'y' * 40
    assert len(store.queue()) == 1
    restarted = MediaStore(tmp_path / 'state.db', root, limit=100)
    restored = restarted.get(record['id'])
    assert restarted.path(restored).read_bytes() == b'x' * 40
    assert 'transfer' not in restored['artifacts']
    assert not partial.exists()
    # A queued item that never finished scanning is reopened as needs_scan, not as a ready transfer.
    assert restored['status'] == 'needs_scan'


def test_encoder_failure_under_a_tiny_cache_leaves_no_transfer_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})

    def convert(src, out, kind, maximum, metadata=None):
        out.write_bytes(b'partial-then-fail')
        raise AudioError('disk full during transfer encode')

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache', limit=50)
    # Reservation of the managed-artifact ceiling cannot fit, so encoding must not publish.
    record = store.import_file('song.mp3', io.BytesIO(b'1234'))
    jobs = JobStore(tmp_path / 'jobs.sqlite')
    try:
        service = MediaService(store, jobs, OperationCoordinator(), HashScanner(), None, device_api=object())
        job = service.create_job('job', [record['id']], 'transfer')
        job.set_status(JobStatus.RUNNING, 'converting')
        with pytest.raises((MediaError, AudioError)):
            service.derivative(record['id'], 'transfer', job)
        assert 'transfer' not in store.get(record['id'])['artifacts']
        assert not (store.root / record['id'] / 'transfer.mp3').exists()
        assert store.path(store.get(record['id'])).read_bytes() == b'1234'
    finally:
        jobs.close()


@pytest.mark.xfail(reason=(
    'BUG: the device track cache stores the list object returned by list_tracks. Mutating '
    'that list during an in-flight transfer makes the next cached GET /api/tracks serve the '
    'partial track without a refresh (test_track_cache_ignores_mutation_of_cached_list_during_transfer).'
))
def test_track_cache_ignores_mutation_of_cached_list_during_transfer(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    monkeypatch.setattr(
        media_service, 'convert_managed',
        lambda src, out, kind, maximum, metadata=None: out.write_bytes(b'full') or out,
    )
    entered, release = threading.Event(), threading.Event()
    shared = [{'id': '1', 'title': 'Complete', 'artist': 'A', 'album': 'B'}]
    calls = {'list': 0}

    def list_tracks(mount):
        calls['list'] += 1
        return shared

    def add_tracks(mount, paths, callback):
        shared.append({'id': 'partial', 'title': 'INCOMPLETE', 'artist': '', 'album': ''})
        entered.set()
        assert release.wait(10)
        return _complete(paths)

    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=HashScanner(), device_api=fake_device(_volume(tmp_path), list_tracks, add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import_ready(client)
        assert [row['id'] for row in client.get('/api/tracks').json()] == ['1']
        holder = {}

        def transfer():
            holder['response'] = client.post('/api/transfers', json={'media_ids': [media_id]})

        worker = threading.Thread(target=transfer)
        worker.start()
        try:
            assert entered.wait(10)
            cached = client.get('/api/tracks').json()
            assert calls['list'] == 1
            assert [row['id'] for row in cached] == ['1']
            assert all(row.get('title') != 'INCOMPLETE' for row in cached)
        finally:
            release.set()
            worker.join(10)
        assert client.get('/api/jobs/' + holder['response'].json()['job_id']).json()['status'] == 'done'
