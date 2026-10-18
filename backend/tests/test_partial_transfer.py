"""Interrupted transfers must not be reported as success, and partial bytes must not look complete."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adversarial_helpers import (
    ORIGIN, TOKEN, ClaimedSize, DisconnectingStream, GrowingStream, HashScanner,
    assert_store_contained, fake_device,
)
from application import create_app
from media_store import MediaError, MediaStore
import jsymphonic
import link_import
import media_service
from transcode import AudioError


def _volume(tmp_path):
    volume = tmp_path / 'device'
    (volume / 'OMGAUDIO').mkdir(parents=True)
    return volume


def _bridge(tmp_path, scanner, api):
    return create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN,
                      origin=ORIGIN, scanner=scanner, device_api=api)


def _import(client, name, payload):
    response = client.post('/api/media/import', files={'files': (name, payload, 'audio/wav')})
    assert response.status_code == 200, response.text
    job = client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    return job['files'][0]['media_id']


def test_truncated_shim_stream_is_not_a_committed_transfer(monkeypatch, tmp_path):
    code = (
        "import json,sys; events="
        + repr([
            {'event': 'start', 'files': 1},
            {'event': 'progress', 'step': 'transfer', 'percent': 100.0, 'speedKBps': 10},
            {'event': 'file', 'step': 'transfer', 'name': 'song.mp3', 'bytes': 4, 'expected': 100},
        ])
        + "; [print(json.dumps(e), flush=True) for e in events]; sys.exit(0)"
    )
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [__import__('sys').executable, '-c', code])
    with pytest.raises(jsymphonic.JSymphonicError) as error:
        jsymphonic.add_tracks(tmp_path, [tmp_path / 'song.mp3'], lambda _event: None)
    assert error.value.needs_reconcile is True
    assert [row.state for row in error.value.result.files] == ['unknown']
    assert error.value.result.files[0].state != 'transferred'


def test_import_size_and_disconnect_do_not_leave_a_complete_file(tmp_path):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache', limit=10_000)
    with pytest.raises(MediaError, match='ended before'):
        store.import_file('short.mp3', ClaimedSize(b'abcd', 80))
    with pytest.raises(MediaError, match='changed size'):
        store.import_file('grew.mp3', GrowingStream(b'abcd', b'EXTRA'))
    with pytest.raises(ConnectionError):
        store.import_file('reset.mp3', DisconnectingStream(b'abcd'))
    assert store.queue() == []
    assert assert_store_contained(store) == []
    assert not any(path.suffix == '.mp3' and path.stat().st_size for path in tmp_path.rglob('*') if path.is_file())


def test_device_disconnect_is_failed_and_blocks_a_blind_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    monkeypatch.setattr(media_service, 'convert_managed', lambda src, out, kind, maximum, metadata=None: out.write_bytes(src.read_bytes() + b'-full') or out)
    seen = []

    def add_tracks(mount, paths, callback):
        callback({'event': 'progress', 'step': 'transfer', 'percent': 37.5})
        seen.extend(paths)
        raise jsymphonic.JSymphonicError('connection reset', needs_reconcile=True)

    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import(client, 'song.wav', b'RIFF-audio')
        response = client.post('/api/transfers', json={'media_ids': [media_id]})
        result = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert result['status'] != 'done'
        assert result['status'] == 'failed'
        assert result['needs_reconcile'] is True
        assert result['files'][0]['state'] == 'unknown'
        assert result['files'][0]['state'] != 'transferred'
        assert 'Verify device state' in result['message']
        assert 'Completed 1 of 1' not in result['message']
        record = app.state.store.get(media_id)
        transfer = record['artifacts']['transfer']
        path = app.state.store.path(record, 'transfer')
        assert transfer['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert transfer['size_bytes'] == path.stat().st_size
        assert path.read_bytes() == b'RIFF-audio-full'
        retry = client.post('/api/transfers', json={'media_ids': [media_id]})
        assert retry.status_code == 409
        assert len(seen) == 1


def test_definite_device_failure_stays_failed_when_a_later_retry_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})

    def convert(src, out, kind, maximum, metadata=None):
        out.write_bytes(b'complete-mp3')
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    attempts = {'n': 0}

    def add_tracks(mount, paths, callback):
        attempts['n'] += 1
        if attempts['n'] == 1:
            raise jsymphonic.JSymphonicError('shim unavailable', needs_reconcile=False)
        return jsymphonic.AddResult(tuple(
            jsymphonic.FileOutcome(index, str(path), 'transferred', 'Device database update completed')
            for index, path in enumerate(paths)
        ))

    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import(client, 'song.wav', b'RIFF-audio')
        first = client.post('/api/transfers', json={'media_ids': [media_id]})
        failed = client.get('/api/jobs/' + first.json()['job_id']).json()
        assert failed['status'] == 'failed'
        assert failed['files'][0]['state'] == 'failed'
        assert failed['needs_reconcile'] is False
        second = client.post('/api/transfers', json={'media_ids': [media_id]})
        assert second.status_code == 200, second.text
        retried = client.get('/api/jobs/' + second.json()['job_id']).json()
        assert retried['status'] == 'done'
        assert retried['files'][0]['state'] == 'transferred'
        assert client.get('/api/jobs/' + first.json()['job_id']).json()['status'] == 'failed'
        record = app.state.store.get(media_id)
        path = app.state.store.path(record, 'transfer')
        assert path.read_bytes() == b'complete-mp3'
        assert record['artifacts']['transfer']['sha256'] == hashlib.sha256(b'complete-mp3').hexdigest()
        assert record['artifacts']['transfer']['size_bytes'] == len(b'complete-mp3')


def test_mixed_batch_is_partial_and_the_failed_file_is_not_left_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})

    def convert(src, out, kind, maximum, metadata=None):
        payload = src.read_bytes()
        if payload == b'second':
            out.write_bytes(b'partial-second')
            raise AudioError('encoder stopped mid-file')
        out.write_bytes(payload + b'-ok')
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    written = []

    def add_tracks(mount, paths, callback):
        written.extend(Path(path).read_bytes() for path in paths)
        return jsymphonic.AddResult(tuple(
            jsymphonic.FileOutcome(index, str(path), 'transferred', 'Device database update completed')
            for index, path in enumerate(paths)
        ))

    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.post('/api/media/import', files=[
            ('files', ('one.wav', b'first', 'audio/wav')),
            ('files', ('two.wav', b'second', 'audio/wav')),
        ])
        imported = client.get('/api/jobs/' + response.json()['job_id']).json()
        ids = [row['media_id'] for row in imported['files']]
        transfer = client.post('/api/transfers', json={'media_ids': ids})
        result = client.get('/api/jobs/' + transfer.json()['job_id']).json()
        by_id = {row['media_id']: row for row in result['files']}
        assert result['status'] == 'partial'
        assert result['status'] != 'done'
        assert by_id[ids[0]]['state'] == 'transferred'
        assert by_id[ids[1]]['state'] == 'failed'
        store = app.state.store
        first = store.get(ids[0])
        second = store.get(ids[1])
        assert store.path(first, 'transfer').read_bytes() == b'first-ok'
        assert first['artifacts']['transfer']['size_bytes'] == len(b'first-ok')
        assert 'transfer' not in second['artifacts']
        assert not (store.root / ids[1] / 'transfer.mp3').exists()
        assert written == [b'first-ok']


def test_cancelled_conversion_removes_the_partial_derivative(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})

    def convert(src, out, kind, maximum, metadata=None):
        out.write_bytes(b'PARTIAL')
        raise AudioError('cancelled mid-transfer')

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], lambda *args: None))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import(client, 'song.wav', b'original-bytes')
        response = client.post('/api/transfers', json={'media_ids': [media_id]})
        result = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert result['status'] == 'failed'
        assert result['files'][0]['state'] != 'transferred'
        store = app.state.store
        record = store.get(media_id)
        assert 'transfer' not in record['artifacts']
        assert not (store.root / media_id / 'transfer.mp3').exists()
        assert store.path(record).read_bytes() == b'original-bytes'
        assert client.get(f'/api/media/{media_id}/stream').content == b'original-bytes'


@pytest.mark.xfail(reason=(
    'BUG: write_device publishes state transferred and MediaService.run marks the job done '
    'without re-checking the transfer artifact after the device write. A checksum and size '
    'change during add_tracks is still reported as success '
    '(test_checksum_mismatch_during_device_write_is_not_success).'
))
def test_checksum_mismatch_during_device_write_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})

    def convert(src, out, kind, maximum, metadata=None):
        out.write_bytes(b'complete-transfer-bytes')
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)

    def add_tracks(mount, paths, callback):
        callback({'event': 'progress', 'step': 'transfer', 'percent': 100.0})
        for path in paths:
            Path(path).write_bytes(b'xx')
        return jsymphonic.AddResult(tuple(
            jsymphonic.FileOutcome(index, str(path), 'transferred', 'Device database update completed')
            for index, path in enumerate(paths)
        ))

    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import(client, 'song.wav', b'RIFF-audio')
        response = client.post('/api/transfers', json={'media_ids': [media_id]})
        result = client.get('/api/jobs/' + response.json()['job_id']).json()
        record = app.state.store.get(media_id)
        path = app.state.store.path(record, 'transfer')
        recorded = record['artifacts']['transfer']
        assert result['status'] != 'done'
        assert result['files'][0]['state'] != 'transferred'
        assert recorded['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert recorded['size_bytes'] == path.stat().st_size


@pytest.mark.xfail(reason=(
    'BUG: an AddResult with needs_reconcile=True and per-file state transferred is stored as '
    'job status done and message "Completed 1 of 1 files". An unconfirmed device write is '
    'reported as success (test_unconfirmed_device_result_is_not_reported_done).'
))
def test_unconfirmed_device_result_is_not_reported_done(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    monkeypatch.setattr(
        media_service, 'convert_managed',
        lambda src, out, kind, maximum, metadata=None: out.write_bytes(b'full') or out,
    )

    def add_tracks(mount, paths, callback):
        return jsymphonic.AddResult(tuple(
            jsymphonic.FileOutcome(index, str(path), 'transferred', 'maybe written')
            for index, path in enumerate(paths)
        ), needs_reconcile=True)

    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], add_tracks))
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        media_id = _import(client, 'song.wav', b'RIFF-audio')
        response = client.post('/api/transfers', json={'media_ids': [media_id]})
        result = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert result['needs_reconcile'] is True
        assert result['status'] != 'done'
        assert result['files'][0]['state'] != 'transferred'
        assert 'Completed 1 of 1' not in result['message']


@pytest.mark.xfail(reason=(
    'BUG: link import keeps the downloader byte counters and can mark the job done even when '
    'the file on disk is shorter than downloaded_bytes/total_bytes. A 5-byte file reported as '
    '100/100 is stored as a completed import '
    '(test_short_download_is_not_marked_complete).'
))
def test_short_download_is_not_marked_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})

    def download(url, folder, max_bytes, progress=None):
        progress({'status': 'downloading', 'downloaded_bytes': 0, 'total_bytes': 100})
        progress({'status': 'finished', 'downloaded_bytes': 100, 'total_bytes': 100})
        path = Path(folder) / 'audio.mp3'
        path.write_bytes(b'short')
        return SimpleNamespace(path=path, name='Track.mp3', metadata={'title': 'T'})

    monkeypatch.setattr(link_import, 'download_link', download)
    app = _bridge(tmp_path, HashScanner(), fake_device(_volume(tmp_path), lambda mount: [], lambda *args: None))
    job_id, media_id = uuid.uuid4().hex, uuid.uuid4().hex
    job = app.state.jobs.create(job_id, 1, kind='link_import')
    job.set_files([dict(file_id=media_id, media_id=media_id, name='Link', state='queued')])
    ticket = app.state.coordinator.admit('import', job_id=job_id)
    app.state.service.run_link_import(job_id, media_id, 'https://youtu.be/dQw4w9WgXcQ', ticket)
    snapshot = app.state.jobs.get(job_id).to_dict()
    record = app.state.store.get(media_id)
    disk = app.state.store.path(record).read_bytes()
    row = snapshot['files'][0]
    assert record['artifacts']['source']['sha256'] == hashlib.sha256(disk).hexdigest()
    assert record['size_bytes'] == len(disk) == 5
    assert row.get('downloaded_bytes') == len(disk)
    assert row.get('total_bytes') in (None, len(disk))
    assert snapshot['status'] != 'done'
    assert record['status'] != 'ready'
