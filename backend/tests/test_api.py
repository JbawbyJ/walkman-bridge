"""Authenticated production route tests; scanner and device injected only in tests."""
import hashlib
import io
import threading
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from main import create_app
from application import parse_range
import device
import jsymphonic
import media_service

TOKEN = 'test-capability-not-a-release-bypass'
ORIGIN = 'http://127.0.0.1:8765'

class Scanner:
    def __init__(self):
        self.calls = []
        self.ok = True
    def scan_audio(self, path):
        self.calls.append(path)
        result = dict(ok=self.ok, state='CLEAN' if self.ok else 'ERROR',
            reason='Clean' if self.ok else 'Defender unavailable', reason_code='clean' if self.ok else 'defender_unavailable',
            defender_status='clean' if self.ok else 'unavailable',
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
        return SimpleNamespace(to_dict=lambda: result)
    def clearance_valid(self, path, record):
        return self.ok and record.get('ok') and record['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    def snapshot(self):
        return {'rows': [], 'engine': 'test scanner'}

@pytest.fixture
def context(tmp_path, monkeypatch):
    volume = tmp_path / 'device'
    (volume / 'OMGAUDIO').mkdir(parents=True)
    tracks = [{'id': '1', 'title': 'Track', 'artist': 'A', 'album': 'B'}]
    calls = dict(list=0, add=0, delete=0, convert=0)
    def list_tracks(mount):
        calls['list'] += 1
        return list(tracks)
    def remove_track(mount, track_id):
        calls['delete'] += 1
        tracks[:] = [t for t in tracks if t['id'] != track_id]
    def add_tracks(mount, paths, callback):
        calls['add'] += 1
        return jsymphonic.AddResult(tuple(jsymphonic.FileOutcome(i, str(p), 'transferred', 'Transferred') for i, p in enumerate(paths)))
    api = SimpleNamespace(find_walkman=lambda: volume, capture_device_identity=device.capture_device_identity,
        device_info=device.device_info, backup_device=device.backup_device,
        list_tracks=list_tracks, remove_track=remove_track, add_tracks=add_tracks, JSymphonicError=jsymphonic.JSymphonicError)
    def convert(src, out, kind, maximum, metadata=None):
        calls['convert'] += 1
        out.write_bytes(src.read_bytes() + b'converted')
        return out
    monkeypatch.setattr(media_service, 'convert_managed', convert)
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    scanner = Scanner()
    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN, scanner=scanner, device_api=api)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        yield SimpleNamespace(app=app, client=client, scanner=scanner, calls=calls, tracks=tracks, api=api)

def imported(ctx, names=('track.wav',)):
    result = ctx.client.post('/api/media/import', files=[('files', (name, b'RIFF audio test', 'audio/wav')) for name in names])
    assert result.status_code == 200, result.text
    return ctx.client.get('/api/jobs/' + result.json()['job_id']).json()

def test_import_retained_after_transfer_duplicate_names(context):
    job = imported(context, ('same.wav', 'same.wav'))
    assert job['status'] == 'done'
    items = context.client.get('/api/queue').json()['items']
    assert len({item['id'] for item in items}) == 2
    assert all(item['name'] == 'same.wav' for item in items)
    assert all('artifacts' not in item for item in items)
    response = context.client.post('/api/transfers', json={'media_ids': [item['id'] for item in items]})
    result = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert [f['state'] for f in result['files']] == ['transferred', 'transferred']
    assert context.calls['add'] == 1 and context.calls['convert'] == 2
    assert len(context.scanner.calls) == 4  # source and derivative each, never metadata-before-scan
    assert len(context.client.get('/api/queue').json()['items']) == 2

def test_denied_clearance_never_reaches_decoder_or_device(context):
    context.scanner.ok = False
    job = imported(context)
    assert job['status'] == 'failed'
    media_id = job['files'][0]['media_id']
    assert context.client.get(f'/api/media/{media_id}/stream').status_code == 423
    context.client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'})
    assert context.calls['convert'] == context.calls['add'] == 0
    assert context.client.post('/api/transfers', json={'media_ids': [media_id]}).status_code == 409

def test_changed_file_invalidates_stream(context):
    job = imported(context)
    media_id = job['files'][0]['media_id']
    store = context.app.state.store
    store.path(store.get(media_id)).write_bytes(b'changed')
    assert context.client.get(f'/api/media/{media_id}/stream').status_code == 423

def test_authenticated_ranges_and_path_traversal(context):
    job = imported(context, ('../../escape.wav',))
    media_id = job['files'][0]['media_id']
    route = f'/api/media/{media_id}/stream'
    response = context.client.get(route, headers={'Range': 'bytes=1-3'})
    assert response.status_code == 206 and response.content == b'IFF'
    assert response.headers['content-range'] == 'bytes 1-3/15'
    assert context.client.head(route).headers['content-length'] == '15'
    assert context.client.get(route, headers={'Range': 'bytes=900-999'}).status_code == 416
    assert context.client.get('/api/media/..%5cstate.db/stream').status_code == 400
    assert context.client.get('/api/engine-busy').json()['busy'] is False
    context.client.headers.pop('X-NightOps-Token')
    assert context.client.get(route).status_code == 403

def test_origin_cookie_and_main_only_boundary(context):
    c = context.client
    assert c.get('/api/health', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert c.get('/api/health', headers={'Host': 'attacker.example'}).status_code == 403
    c.headers.pop('X-NightOps-Token')
    c.cookies.set('nightops_session', TOKEN)
    assert c.get('/api/health').status_code == 200
    assert c.get('/api/health', headers={'Sec-Fetch-Site': 'same-site'}).status_code == 403
    assert c.get('/api/internal/scanner/pending').status_code == 403
    assert c.post('/api/shutdown/drain', headers={'Origin': ORIGIN}).status_code == 403
    assert c.patch('/api/queue/order', json={'ids': []}).status_code == 403
    assert c.patch('/api/queue/order', json={'ids': []}, headers={'Origin': ORIGIN}).status_code == 200

def test_etag_rejects_stale_deletion_before_write(context):
    c = context.client
    etag = c.get('/api/tracks').headers['etag']
    assert c.delete('/api/tracks/1').status_code == 428
    context.tracks.append({'id': '2', 'title': 'Another'})
    assert c.delete('/api/tracks/1', headers={'If-Match': etag}).status_code == 409
    assert context.calls['delete'] == 0
    fresh = c.get('/api/tracks?refresh=1').headers['etag']
    assert c.delete('/api/tracks/1', headers={'If-Match': fresh}).status_code == 200
    assert len(c.get('/api/tracks').json()) == 1

def test_transfer_job_passes_fatal_code(context):
    def coded(*args):
        raise jsymphonic.JSymphonicError('journal open', code='PLAYLIST_JOURNAL_PENDING', needs_reconcile=True)
    context.api.add_tracks = coded
    job = imported(context)
    response = context.client.post('/api/transfers', json={'media_ids': [job['files'][0]['media_id']]})
    result = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert result['files'][0]['fatal_code'] == 'PLAYLIST_JOURNAL_PENDING'
    assert result['files'][0]['detail'] == 'journal open'


def test_transfer_job_does_not_invent_fatal_code_from_message(context):
    def generic(*args):
        raise jsymphonic.JSymphonicError('see PLAYLIST_REF_MISSING', needs_reconcile=True)
    context.api.add_tracks = generic
    job = imported(context)
    response = context.client.post('/api/transfers', json={'media_ids': [job['files'][0]['media_id']]})
    result = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert result['files'][0]['fatal_code'] is None
    assert 'PLAYLIST_REF_MISSING' in result['files'][0]['detail']


def test_track_read_and_delete_pass_fatal_code(context, monkeypatch):
    from test_jsymphonic import scripted
    scripted(monkeypatch, [{'event': 'fatal', 'message': 'journal open', 'code': 'PLAYLIST_JOURNAL_PENDING'}], exit_code=1)
    context.api.list_tracks = jsymphonic.list_tracks
    listed = context.client.get('/api/tracks')
    assert listed.status_code == 409
    assert listed.json()['detail'] == {'message': 'journal open', 'fatal_code': 'PLAYLIST_JOURNAL_PENDING'}
    context.api.list_tracks = lambda mount: list(context.tracks)
    context.api.remove_track = jsymphonic.remove_track
    etag = context.client.get('/api/tracks').headers['etag']
    deleted = context.client.delete('/api/tracks/1', headers={'If-Match': etag})
    assert deleted.status_code == 409, deleted.text
    assert deleted.json()['detail']['fatal_code'] == 'PLAYLIST_JOURNAL_PENDING'
    assert deleted.json()['detail']['code'] == 'verify_device_state'
    job = context.client.get('/api/jobs/' + deleted.json()['detail']['job_id']).json()
    assert job['files'][0]['fatal_code'] == 'PLAYLIST_JOURNAL_PENDING'


def test_uncertain_add_is_never_reported_as_nothing_written(context):
    def fail(*args):
        raise jsymphonic.JSymphonicError('Write interrupted', needs_reconcile=True)
    context.api.add_tracks = fail
    job = imported(context)
    response = context.client.post('/api/transfers', json={'media_ids': [job['files'][0]['media_id']]})
    result = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert result['needs_reconcile'] is True
    assert result['files'][0]['state'] == 'unknown'
    assert 'Verify device state' in result['message']

def test_drain_counts_conversion_and_refuses_new_admission(context, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def convert(src, out, kind, maximum, metadata=None):
        entered.set()
        assert release.wait(10)
        out.write_bytes(b'derivative')
        return out
    monkeypatch.setattr(media_service, 'convert_managed', convert)
    job = imported(context)
    media_id = job['files'][0]['media_id']
    thread = threading.Thread(target=lambda: context.client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'}))
    thread.start()
    try:
        assert entered.wait(10)
        assert context.client.get('/api/engine-busy').json()['busy'] is True
        assert context.client.post('/api/shutdown/drain').json()['draining'] is True
        assert context.client.post('/api/media/import', files={'files': ('new.wav', b'no')}).status_code == 409
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert context.client.get('/api/engine-busy').json()['busy'] is False

def test_limits_reject_before_multipart_persistence(context, monkeypatch):
    assert context.client.post('/api/media/import', content=b'x', headers={'Content-Length': str(3 * 1024**3)}).status_code == 413
    import security
    monkeypatch.setattr(security, 'MAX_FILE', 5)
    assert context.client.post('/api/media/import', files={'files': ('a.wav', b'123456')}).status_code == 413
    assert context.client.get('/api/queue').json()['items'] == []
    assert context.client.get('/api/engine-busy').json()['busy'] is False

def test_player_has_no_device_routes(tmp_path):
    app = create_app(product='player', data_dir=tmp_path, token=TOKEN, origin=ORIGIN, scanner=Scanner())
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        assert client.get('/api/health').json()['java'] is False
        assert client.get('/api/device').status_code == 404
        assert client.post('/api/transfers', json={'media_ids': ['a']}).status_code in (404, 405)

@pytest.mark.parametrize('value', ['bytes=-0', 'bytes=2-1', 'bytes=1-2,4-5', 'bytes=a-b'])
def test_bad_ranges(value):
    with pytest.raises(Exception) as exc:
        parse_range(value, 10)
    assert exc.value.status_code == 416
