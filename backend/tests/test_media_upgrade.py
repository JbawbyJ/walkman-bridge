"""Public import/transfer contracts; network and scanner injected at their boundaries."""
import hashlib
import threading
from types import SimpleNamespace

import pytest

import media_service
from jobs import JobStore, JobStatus
from media_store import MediaStore
from media_store import MediaError
from test_api import context, imported


def test_transfer_writes_filename_fallback_instead_of_cache_labels(context, monkeypatch):
    job = imported(context, ('My recording.wav',))
    media_id = job['files'][0]['media_id']
    supplied = []

    def convert(src, out, kind, maximum, metadata=None):
        supplied.append(metadata)
        out.write_bytes(src.read_bytes() + b'converted')

    monkeypatch.setattr(media_service, 'convert_managed', convert)
    response = context.client.post('/api/transfers', json={'media_ids': [media_id]})
    assert response.status_code == 200
    assert supplied[0]['title'] == 'My recording'
    assert supplied[0]['artist'] == 'Unknown artist'
    assert supplied[0]['album'] == 'Unknown album'


def test_link_import_uses_scan_gate_and_keeps_provider_metadata(context, monkeypatch):
    import link_import
    decoded = []

    def download(url, directory, max_bytes, progress=None):
        path = directory / 'download.m4a'
        path.write_bytes(b'fake downloaded audio')
        return SimpleNamespace(path=path, name='Remote song.m4a',
                               metadata={'title': 'Remote song', 'artist': 'Remote artist', 'album': 'Release'})

    def analyze(path):
        assert path in context.scanner.calls
        decoded.append(path)
        return {'duration_seconds': 42}

    monkeypatch.setattr(link_import, 'download_link', download)
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', analyze)
    response = context.client.post('/api/media/import-link', json={'url': 'https://www.youtube.com/watch?v=BaW_jenozKc'})
    assert response.status_code == 200, response.text
    job = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    assert job['kind'] == 'link_import'
    assert len(decoded) == 1
    item = context.client.get('/api/queue').json()['items'][0]
    assert (item['title'], item['artist'], item['album']) == ('Remote song', 'Remote artist', 'Release')
    assert job['files'][0]['file_id'] == item['id']
    assert not list(context.app.state.store.root.glob('download-*'))


def test_link_download_never_decodes_when_defender_rejects(context, monkeypatch):
    import link_import
    context.scanner.ok = False

    def download(url, directory, max_bytes, progress=None):
        path = directory / 'download.mp3'
        path.write_bytes(b'downloaded bytes')
        return SimpleNamespace(path=path, name='song.mp3', metadata={})

    monkeypatch.setattr(link_import, 'download_link', download)
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: pytest.fail('Uncleared decoder called'))
    response = context.client.post('/api/media/import-link', json={'url': 'https://soundcloud.com/artist/song'})
    job = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'failed'
    media_id = job['files'][0]['media_id']
    assert context.client.get(f'/api/media/{media_id}/stream').status_code == 423
    assert context.calls['add'] == context.calls['convert'] == 0


def test_link_import_rejects_local_url_before_job_or_download(context):
    response = context.client.post('/api/media/import-link', json={'url': 'https://127.0.0.1/private'})
    assert response.status_code == 400
    assert context.app.state.jobs.latest() is None


def test_link_import_enforces_small_json_request_limit(context):
    response = context.client.post('/api/media/import-link', content=b'x' * 9000,
        headers={'Content-Type': 'application/json'})
    assert response.status_code == 413


def test_link_failure_is_durable_and_releases_admission(context, monkeypatch):
    import link_import
    monkeypatch.setattr(link_import, 'download_link', lambda *a, **kw: (_ for _ in ()).throw(link_import.LinkImportError('Download unavailable')))
    response = context.client.post('/api/media/import-link', json={'url': 'https://soundcloud.com/artist/song'})
    job = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'failed'
    assert job['files'][0]['state'] == 'failed'
    assert 'Download unavailable' in job['files'][0]['detail']
    assert not context.client.get('/api/engine-busy').json()['busy']
    assert not list(context.app.state.store.root.glob('download-*'))


def test_artwork_is_never_served_without_its_own_clearance(context):
    job = imported(context)
    media_id = job['files'][0]['media_id']
    store = context.app.state.store
    record = store.get(media_id)
    path = store.root / media_id / 'artwork.jpg'
    path.write_bytes(b'\xff\xd8\xff\xe0fake\xff\xd9')
    record['artifacts']['artwork'] = dict(name=path.name, size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), scan=None)
    store.update(media_id, artifacts=record['artifacts'], artwork_status='ready')
    assert context.client.get(f'/api/media/{media_id}/artwork').status_code == 423


def test_downloading_job_recovers_as_interrupted_and_partial_file_removed(tmp_path):
    database, cache = tmp_path / 'state.sqlite', tmp_path / 'cache'
    cache.mkdir()
    job_id = 'a' * 32
    folder = cache / ('download-' + job_id)
    folder.mkdir()
    (folder / 'audio.mp3').write_bytes(b'partial')
    jobs = JobStore(database)
    job = jobs.create(job_id, 1, kind='link_import')
    job.set_status(JobStatus.RUNNING, 'Downloading')
    job.set_files([dict(file_id='b'*32, media_id='b'*32, state='downloading')], phase='downloading')
    jobs.close()
    recovered = JobStore(database)
    recovered.recover_interrupted()
    MediaStore(database, cache)
    result = recovered.get(job_id).to_dict()
    assert result['status'] == result['files'][0]['state'] == 'interrupted'
    assert not result['needs_reconcile']
    assert not folder.exists()
    recovered.close()


def test_drain_waits_for_downloading_and_refuses_other_import(context, monkeypatch):
    import link_import
    entered, release = threading.Event(), threading.Event()
    def download(url, directory, maximum, progress=None):
        entered.set()
        assert release.wait(10)
        raise link_import.LinkImportError('Test download stopped')
    monkeypatch.setattr(link_import, 'download_link', download)
    thread = threading.Thread(target=lambda: context.client.post('/api/media/import-link',
        json={'url': 'https://soundcloud.com/artist/song'}))
    thread.start()
    try:
        assert entered.wait(5)
        assert context.client.post('/api/media/import', files={'files': ('x.wav', b'x')}).status_code == 409
        draining = context.client.post('/api/shutdown/drain').json()
        assert draining['busy'] and draining['draining']
        assert context.client.post('/api/media/import-link', json={'url': 'https://soundcloud.com/artist/song'}).status_code == 409
    finally:
        release.set()
        thread.join(10)
    assert not context.client.get('/api/engine-busy').json()['busy']


def test_cleared_artwork_is_authenticated_and_changed_image_is_blocked(context):
    job = imported(context)
    media_id = job['files'][0]['media_id']
    store = context.app.state.store
    record = store.get(media_id)
    path = store.root / media_id / 'artwork.jpg'
    content = b'\xff\xd8\xff\xe0fake\xff\xd9'
    path.write_bytes(content)
    record['artifacts']['artwork'] = dict(name=path.name, size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(), scan=context.scanner.scan_audio(path).to_dict())
    store.update(media_id, artifacts=record['artifacts'], artwork_status='ready')
    response = context.client.get(f'/api/media/{media_id}/artwork')
    assert response.status_code == 200 and response.content == content
    assert response.headers['content-type'] == 'image/jpeg'
    assert context.client.get(f'/api/media/{media_id}/artwork', headers={'Origin': 'https://hostile.example'}).status_code == 403
    path.write_bytes(content + b'tampered')
    assert context.client.get(f'/api/media/{media_id}/artwork').status_code == 423
    assert store.get(media_id)['status'] == 'ready'  # failed optional art does not falsify audio clearance


def test_metadata_persists_only_normalized_tags_and_measured_fields(context, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: dict(
        title='A\u202e title', genre='\x00', date='not a date', year='99999', track='song title',
        duration_seconds=3.5, integrated_lufs=-18.0, has_artwork=False, unexpected='discard'))
    job = imported(context)
    record = context.app.state.store.get(job['files'][0]['media_id'])
    assert record['title'] == 'A title'
    assert all(record.get(key) is None for key in ('genre', 'date', 'year', 'track'))
    assert record['duration_seconds'] == 3.5 and record['integrated_lufs'] == -18.0
    assert 'unexpected' not in record


def test_source_clearance_failure_during_optional_artwork_fails_import(context, monkeypatch):
    def analyze(path):
        context.scanner.ok = False
        return {'has_artwork': True}
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', analyze)
    monkeypatch.setattr(media_service, 'extract_cleared_artwork', lambda *a: pytest.fail('Uncleared image decoded'))
    job = imported(context)
    assert job['status'] == 'failed' and job['files'][0]['state'] == 'failed'
    assert context.app.state.store.get(job['files'][0]['media_id'])['status'] == 'failed'
