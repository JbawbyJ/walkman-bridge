"""Audit regressions: derivative verdicts, dangling derivative entries and ticket release."""
from __future__ import annotations

import contextlib
import hashlib
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adversarial_helpers import ORIGIN, TOKEN, HashScanner, fake_device
from application import create_app
from jobs import JobStatus
from media_store import MediaStore
import jsymphonic
import link_import
import media_service

HEADERS = {'X-NightOps-Token': TOKEN}


class PathScanner(HashScanner):
    """Hash-bound clearance double that fails, or crashes, only for the named managed files."""

    def __init__(self):
        super().__init__()
        self.failing, self.crashing = set(), set()

    def scan_audio(self, path):
        if path.name in self.crashing:
            raise RuntimeError('Defender scan crashed')
        scan = super().scan_audio(path)
        if path.name not in self.failing:
            return scan
        result = dict(scan.to_dict(), ok=False, state='ERROR', reason='Defender unavailable',
                      reason_code='defender_unavailable', defender_status='unavailable')
        return SimpleNamespace(to_dict=lambda: dict(result))

    def clearance_valid(self, path, record, trusted_status=None):
        if path.name in self.failing | self.crashing:
            return False
        return super().clearance_valid(path, record, trusted_status)


def _transferred(paths):
    return jsymphonic.AddResult(tuple(
        jsymphonic.FileOutcome(index, str(path), 'transferred', 'Device database update completed')
        for index, path in enumerate(paths)))


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    conversions = []

    def convert(src, out, kind, maximum, metadata=None):
        conversions.append(kind)
        out.write_bytes(src.read_bytes() + f'-{kind}-{len(conversions)}'.encode())
        return out

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    written = []

    def add_tracks(mount, paths, callback):
        written.extend(Path(path).read_bytes() for path in paths)
        return _transferred(paths)

    volume = tmp_path / 'device'
    (volume / 'OMGAUDIO').mkdir(parents=True)
    scanner = PathScanner()
    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=scanner, device_api=fake_device(volume, lambda mount: [], add_tracks))
    return SimpleNamespace(app=app, scanner=scanner, conversions=conversions, written=written,
                           store=app.state.store, service=app.state.service,
                           coordinator=app.state.coordinator, jobs=app.state.jobs)


@pytest.fixture
def client(bridge):
    with TestClient(bridge.app, base_url=ORIGIN, headers=HEADERS) as client:
        yield client


def _import(client, payload=b'RIFF-audio'):
    response = client.post('/api/media/import', files={'files': ('song.wav', payload, 'audio/wav')})
    assert response.status_code == 200, response.text
    job = client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    return job['files'][0]['media_id']


def _job(client, response):
    assert response.status_code == 200, response.text
    return client.get('/api/jobs/' + response.json()['job_id']).json()


def _queued(client, media_id):
    return next(item for item in client.get('/api/queue').json()['items'] if item['id'] == media_id)


# Finding 1: a derivative verdict never relabels the cleared item it was made from.

@pytest.mark.parametrize('failure', ['verdict', 'crash'])
def test_failed_transfer_mp3_scan_keeps_the_cleared_item_ready_and_transferable(bridge, client, failure):
    media_id = _import(client)
    (bridge.scanner.failing if failure == 'verdict' else bridge.scanner.crashing).add('transfer.mp3')
    job = _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))
    assert job['status'] == 'failed'
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['reason_code'] == ('defender_unavailable' if failure == 'verdict' else 'scan_failed')
    assert bridge.written == []
    # The source clearance is intact, so the item stays playable and transferable.
    item = _queued(client, media_id)
    assert item['status'] == 'ready' and item['scan']['ok'] is True
    record = bridge.store.get(media_id)
    assert record['status'] == 'ready' and record['scan']['ok'] is True
    if failure == 'verdict':
        assert record['artifacts']['transfer']['scan']['reason_code'] == 'defender_unavailable'
    assert client.get(f'/api/media/{media_id}/stream').content == b'RIFF-audio'
    bridge.scanner.failing.clear()
    bridge.scanner.crashing.clear()
    retry = _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))
    assert retry['status'] == 'done' and retry['files'][0]['state'] == 'transferred'
    assert len(bridge.written) == 1


def test_failed_playback_flac_scan_during_prepare_keeps_the_source_ready(bridge, client):
    media_id = _import(client)
    bridge.scanner.failing.add('playback.flac')
    job = _job(client, client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'}))
    assert job['status'] == 'failed' and job['files'][0]['state'] == 'failed'
    record = bridge.store.get(media_id)
    assert record['status'] == 'ready' and record['scan']['ok'] is True
    assert record['playback'] == 'source' and record['mime'] == 'audio/wav'
    assert record['artifacts']['playback']['scan']['ok'] is False
    item = _queued(client, media_id)
    assert item['status'] == 'ready' and item['scan']['ok'] is True
    assert client.get(f'/api/media/{media_id}/stream').content == b'RIFF-audio'


def test_failed_rescan_of_the_served_playback_flac_leaves_the_item_not_ready(bridge, client):
    media_id = _import(client)
    prepared = _job(client, client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'}))
    assert prepared['status'] == 'done'
    assert bridge.store.get(media_id)['playback'] == 'playback'
    bridge.scanner.failing.add('playback.flac')
    rescan = _job(client, client.post(f'/api/media/{media_id}/rescan'))
    assert rescan['status'] == 'failed'
    record = bridge.store.get(media_id)
    assert record['playback'] == 'playback'
    assert record['status'] == 'failed' and record['scan']['ok'] is False
    assert _queued(client, media_id)['status'] != 'ready'
    assert client.get(f'/api/media/{media_id}/stream').status_code == 423


# Finding 2: an entry whose file is gone is regenerated instead of failing every later transfer.

def test_transfer_regenerates_a_transfer_entry_whose_file_is_missing(bridge, client):
    media_id = _import(client)
    first = _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))
    assert first['status'] == 'done'
    stale = bridge.store.root / media_id / 'transfer.mp3'
    stale.unlink()  # a crash after the unlink, or Defender/the user removing the file out of band
    assert 'transfer' in bridge.store.get(media_id)['artifacts']
    second = _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))
    assert second['status'] == 'done', second
    assert second['files'][0]['state'] == 'transferred'
    record = bridge.store.get(media_id)
    path = bridge.store.path(record, 'transfer')
    assert path.read_bytes() == b'RIFF-audio-transfer-2' == bridge.written[-1]
    assert record['artifacts']['transfer']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record['artifacts']['transfer']['size_bytes'] == path.stat().st_size
    assert record['artifacts']['transfer']['scan']['ok'] is True
    assert record['status'] == 'ready'


def test_a_crash_while_replacing_a_stale_transfer_never_leaves_an_entry_without_its_file(bridge, client, monkeypatch):
    media_id = _import(client)
    assert _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))['status'] == 'done'
    bridge.store.edit_metadata(media_id, {'title': 'Renamed'})  # the stored transfer.mp3 is now stale
    store, real_update, armed = bridge.store, bridge.store.update, [True]

    def crashing_update(target, **changes):
        if armed and 'transfer' not in changes.get('artifacts', {'transfer': None}):
            armed.clear()
            raise RuntimeError('process stopped before the commit')
        return real_update(target, **changes)

    monkeypatch.setattr(store, 'update', crashing_update)
    crashed = _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))
    assert not armed and crashed['status'] == 'failed'
    record = store.get(media_id)
    path = store.root / media_id / 'transfer.mp3'
    assert 'transfer' not in record['artifacts'] or path.exists()
    monkeypatch.setattr(store, 'update', real_update)
    retry = _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))
    assert retry['status'] == 'done', retry
    record = store.get(media_id)
    assert record['artifacts']['transfer']['metadata_revision'] == record['metadata_revision'] == 1
    assert record['artifacts']['transfer']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_unreferenced_transfer_left_by_a_crash_after_the_commit_is_removed_at_startup(bridge, client, monkeypatch, tmp_path):
    media_id = _import(client)
    assert _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))['status'] == 'done'
    bridge.store.edit_metadata(media_id, {'title': 'Renamed'})
    path = bridge.store.root / media_id / 'transfer.mp3'
    real_unlink = Path.unlink

    def crashing_unlink(self, *args, **kwargs):
        if self == path:
            raise RuntimeError('process stopped before the unlink')
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', crashing_unlink)
    assert _job(client, client.post('/api/transfers', json={'media_ids': [media_id]}))['status'] == 'failed'
    monkeypatch.setattr(Path, 'unlink', real_unlink)
    assert path.exists() and 'transfer' not in bridge.store.get(media_id)['artifacts']
    restarted = MediaStore(tmp_path / 'state' / 'nightops.sqlite', bridge.store.root)
    assert not path.exists() and restarted.path(restarted.get(media_id)).read_bytes() == b'RIFF-audio'


def test_prepare_regenerates_a_missing_served_playback_flac_and_falls_back_to_the_source_meanwhile(bridge, client, monkeypatch):
    media_id = _import(client)
    assert _job(client, client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'}))['status'] == 'done'
    (bridge.store.root / media_id / 'playback.flac').unlink()
    real_convert = media_service.convert_managed

    def failing_convert(src, out, kind, maximum, metadata=None):
        raise media_service.MediaError('encoder unavailable')

    monkeypatch.setattr(media_service, 'convert_managed', failing_convert)
    failed = _job(client, client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'}))
    assert failed['status'] == 'failed'
    record = bridge.store.get(media_id)
    # The dropped entry is no longer served: playback points back at the cleared source.
    assert 'playback' not in record['artifacts'] and (record['playback'], record['mime']) == ('source', 'audio/wav')
    assert client.get(f'/api/media/{media_id}/stream').content == b'RIFF-audio'
    monkeypatch.setattr(media_service, 'convert_managed', real_convert)
    assert _job(client, client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'}))['status'] == 'done'
    record = bridge.store.get(media_id)
    assert record['playback'] == 'playback' and record['status'] == 'ready'
    assert client.get(f'/api/media/{media_id}/stream').content == bridge.store.path(record, 'playback').read_bytes()


# Finding 3: the coordinator ticket is finished whatever the lease release or job store does.
# These tests drive the service directly so that a leaked ticket fails an assertion instead of
# hanging the TestClient lifespan's drain.

def _failing_release(service, monkeypatch):
    real_hold, calls = service.hold, []

    def hold(ids):
        stack = real_hold(ids)
        real_close = stack.close
        calls.append(ids)

        def close():
            real_close()
            if len(calls) == 1:
                raise PermissionError(13, 'The process cannot access the file because it is being used by another process')
        stack.close = close
        return stack

    monkeypatch.setattr(service, 'hold', hold)
    return calls


def _admitted_import(bridge, client):
    response = client.post('/api/media/import', files={'files': ('next.wav', b'RIFF-next', 'audio/wav')})
    assert response.status_code == 200, response.text
    assert client.get('/api/jobs/' + response.json()['job_id']).json()['status'] == 'done'
    assert bridge.coordinator.snapshot()['busy'] is False


def test_a_failing_lease_release_still_finishes_the_ticket(bridge, monkeypatch):
    calls = _failing_release(bridge.service, monkeypatch)
    media_id = bridge.store.import_file('song.wav', io.BytesIO(b'RIFF-audio'))['id']
    job_id = uuid.uuid4().hex
    bridge.service.create_job(job_id, [media_id], 'import')
    ticket = bridge.coordinator.admit('import', job_id=job_id)
    leases = bridge.service.hold([media_id])
    with pytest.raises(PermissionError):
        bridge.service.run(job_id, [media_id], ticket, leases, 'import')
    assert calls == [[media_id]]
    assert bridge.jobs.get(job_id).to_dict()['status'] == 'done'
    assert bridge.coordinator.snapshot()['busy'] is False
    _admitted_import(bridge, TestClient(bridge.app, base_url=ORIGIN, headers=HEADERS))


def test_a_job_store_failure_before_processing_still_finishes_the_ticket(bridge):
    media_id = bridge.store.import_file('song.wav', io.BytesIO(b'RIFF-audio'))['id']
    job_id = uuid.uuid4().hex
    job = bridge.service.create_job(job_id, [media_id], 'rescan')
    ticket = bridge.coordinator.admit('rescan', job_id=job_id)
    leases = bridge.service.hold([media_id])
    real_set_status = job.set_status

    def set_status(status, message):
        if status == JobStatus.RUNNING:
            raise RuntimeError('job store unavailable')
        return real_set_status(status, message)

    job.set_status = set_status
    with contextlib.suppress(RuntimeError):
        bridge.service.run(job_id, [media_id], ticket, leases, 'rescan')
    assert bridge.coordinator.snapshot()['busy'] is False
    assert job.to_dict()['status'] == 'failed'
    bridge.service.hold([media_id]).close()  # the per-media work admission was released too
    _admitted_import(bridge, TestClient(bridge.app, base_url=ORIGIN, headers=HEADERS))


def _link_job(bridge):
    job_id, media_id = uuid.uuid4().hex, uuid.uuid4().hex
    job = bridge.jobs.create(job_id, 1, kind='link_import')
    job.set_files([dict(file_id=media_id, media_id=media_id, name='Link import', state='queued')])
    return job_id, media_id, bridge.coordinator.admit('import', job_id=job_id)


def _download(payload, total):
    def download(url, folder, max_bytes, progress=None):
        progress({'status': 'finished', 'downloaded_bytes': total, 'total_bytes': total})
        path = Path(folder) / 'audio.mp3'
        path.write_bytes(payload)
        return SimpleNamespace(path=path, name='Track.mp3', metadata={'title': 'Track'})
    return download


def test_an_error_after_link_import_delegation_does_not_relabel_the_done_job(bridge, monkeypatch):
    monkeypatch.setattr(link_import, 'download_link', _download(b'ID3-complete', len(b'ID3-complete')))
    _failing_release(bridge.service, monkeypatch)
    job_id, media_id, ticket = _link_job(bridge)
    with contextlib.suppress(PermissionError):
        bridge.service.run_link_import(job_id, media_id, 'https://youtu.be/dQw4w9WgXcQ', ticket)
    job = bridge.jobs.get(job_id).to_dict()
    assert job['status'] == 'done', job
    assert job['files'][0]['state'] == 'ready' and job['files'][0].get('reason_code') != 'link_import_failed'
    assert bridge.store.get(media_id)['status'] == 'ready'
    assert bridge.coordinator.snapshot()['busy'] is False
    _admitted_import(bridge, TestClient(bridge.app, base_url=ORIGIN, headers=HEADERS))


def test_a_failing_lease_release_before_link_import_delegation_still_finishes_the_ticket(bridge, monkeypatch):
    monkeypatch.setattr(link_import, 'download_link', _download(b'short', 100))
    _failing_release(bridge.service, monkeypatch)
    job_id, media_id, ticket = _link_job(bridge)
    with pytest.raises(PermissionError):
        bridge.service.run_link_import(job_id, media_id, 'https://youtu.be/dQw4w9WgXcQ', ticket)
    job = bridge.jobs.get(job_id).to_dict()
    assert job['status'] == 'failed' and job['files'][0]['reason_code'] == 'link_import_failed'
    assert bridge.coordinator.snapshot()['busy'] is False
    _admitted_import(bridge, TestClient(bridge.app, base_url=ORIGIN, headers=HEADERS))
