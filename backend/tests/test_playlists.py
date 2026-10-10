"""Saved playlist and metadata contracts; all audio/device boundaries are fake."""
import threading
from types import SimpleNamespace

import pytest

from media_store import MediaStore, MediaError
from jobs import JobStore
import jsymphonic
from test_api import context, imported
from test_jsymphonic import scripted


def test_saved_playlist_order_persists_and_delete_keeps_audio(context):
    ctx = context
    imported(ctx, ('one.wav', 'two.wav'))
    ids = [row['id'] for row in ctx.client.get('/api/queue').json()['items']]
    made = ctx.client.post('/api/playlists', json={'name': '  Night\u202e drive  ', 'media_ids': ids[::-1]})
    assert made.status_code == 201, made.text
    playlist = made.json()
    assert playlist['name'] == 'Night drive'
    assert playlist['media_ids'] == ids[::-1]
    restored = MediaStore(ctx.app.state.store.root.parent / 'nightops.sqlite', ctx.app.state.store.root)
    assert restored.playlists()[0] == playlist
    result = ctx.client.delete('/api/playlists/' + playlist['id'], headers={'If-Match': playlist['etag']})
    assert result.status_code == 200
    assert len(ctx.app.state.store.queue()) == 2
    assert all(ctx.app.state.store.path(row).exists() for row in ctx.app.state.store.queue())


def test_playlist_membership_rejects_stale_and_unknown_and_library_removal_prunes(context):
    imported(context, ('one.wav', 'two.wav'))
    ids = [row['id'] for row in context.app.state.store.queue()]
    c = context.client
    playlist = c.post('/api/playlists', json={'name': 'Set', 'media_ids': ids}).json()
    url = '/api/playlists/' + playlist['id']
    assert c.patch(url, json={'name': 'New'}).status_code == 428
    assert c.patch(url, headers={'If-Match': playlist['etag']}, json={'media_ids': ['a' * 32]}).status_code == 400
    assert c.patch(url, headers={'If-Match': playlist['etag']}, json={'media_ids': ids * 2}).status_code == 400
    changed = c.patch(url, headers={'If-Match': playlist['etag']}, json={'media_ids': ids[::-1]}).json()
    assert changed['media_ids'] == ids[::-1]
    assert c.delete(url, headers={'If-Match': playlist['etag']}).status_code == 409
    with context.app.state.store.lease(ids[0]) as source:
        assert c.delete('/api/queue/items/' + ids[0]).status_code == 200
        assert context.app.state.store.path(source).exists()
        pruned = c.get(url).json()
        assert pruned['media_ids'] == [ids[1]]
        assert pruned['revision'] == changed['revision'] + 1
        assert c.patch(url, headers={'If-Match': pruned['etag']}, json={'media_ids': ids}).status_code == 400
    assert c.get('/api/playlists').json()['items'] == [pruned]


def test_metadata_edit_preserves_source_and_lease_and_rejects_work(context):
    imported(context)
    store = context.app.state.store
    record = store.queue()[0]
    media_id = record['id']
    url = f'/api/media/{media_id}/metadata'
    before = store.path(record).read_bytes()
    with store.lease(media_id):
        updated = context.client.patch(url, json={'title': '  Real\u202e song  ', 'artist': 'Artist', 'year': '2024', 'track': '02/10'})
        assert updated.status_code == 200, updated.text
        assert updated.json()['title'] == 'Real song'
        assert updated.json()['year'] == 2024
        assert updated.json()['track'] == '2/10'
        assert updated.json()['name'] == record['name']
        assert updated.json()['metadata_revision'] == 1
        assert store.path(store.get(media_id)).read_bytes() == before
    with store.work([media_id]):
        assert context.client.patch(url, json={'title': 'Busy'}).status_code == 409
    assert context.client.patch(url, json={'year': 'oops'}).status_code == 400
    assert context.client.patch(url, json={'year': 0}).status_code == 400
    assert context.client.patch(url, json={'track': '4/2'}).status_code == 400
    assert context.client.patch(url, json={'source': 'C:/anything'}).status_code == 422
    assert context.client.patch(url, json={}).status_code == 400
    cleared = context.client.patch(url, json={'year': '', 'track': '', 'genre': ''}).json()
    assert cleared['year'] is None and cleared['date'] is None and cleared['track'] is None


def native_context(ctx):
    rows = []
    calls = []
    ctx.api.list_playlists = lambda mount: [dict(row, track_ids=list(row['track_ids'])) for row in rows]
    def create(mount, name, ids):
        calls.append(('create', name, ids))
        row = {'id': str(len(rows) + 1), 'name': name, 'track_ids': list(ids)}
        rows.append(row)
        return row
    def update(mount, playlist_id, name=None, track_ids=None):
        calls.append(('update', playlist_id, name, track_ids))
        row = next(row for row in rows if row['id'] == playlist_id)
        if name is not None:
            row['name'] = name
        if track_ids is not None:
            row['track_ids'] = list(track_ids)
        return dict(row)
    def delete(mount, playlist_id):
        calls.append(('delete', playlist_id))
        rows[:] = [row for row in rows if row['id'] != playlist_id]
    ctx.api.create_playlist, ctx.api.update_playlist, ctx.api.delete_playlist = create, update, delete
    return SimpleNamespace(rows=rows, calls=calls)


def test_native_crud_guards_track_ledger_and_persists_job(context):
    native = native_context(context)
    c = context.client
    url = '/api/device/playlists'
    initial = c.get(url)
    assert initial.status_code == 200, initial.text
    assert initial.json() == {'items': []}
    assert c.post(url, json={'name': 'Walk', 'track_ids': ['1']}).status_code == 428
    context.tracks.append({'id': '2', 'title': 'New track'})
    assert c.post(url, headers={'If-Match': initial.headers['etag']}, json={'name': 'Walk', 'track_ids': ['1']}).status_code == 409
    assert native.calls == []
    fresh = c.get(url).headers['etag']
    assert c.post(url, headers={'If-Match': fresh}, json={'name': 'Walk', 'track_ids': ['99']}).status_code == 400
    made = c.post(url, headers={'If-Match': fresh}, json={'name': 'Walk', 'track_ids': ['2', '1']})
    assert made.status_code == 201, made.text
    playlist = made.json()['playlist']
    assert playlist['track_ids'] == ['2', '1']
    job = c.get('/api/jobs/' + made.json()['job_id']).json()
    assert job['status'] == 'done' and job['phase'] == 'finished'
    assert job['files'][0]['state'] == 'transferred' and not job['needs_reconcile']
    etag = c.get(url).headers['etag']
    updated = c.patch(url + '/' + playlist['id'], headers={'If-Match': etag}, json={'name': 'Renamed', 'track_ids': []})
    assert updated.status_code == 200, updated.text
    assert updated.json()['playlist']['track_ids'] == []
    assert c.delete(url + '/' + playlist['id'], headers={'If-Match': etag}).status_code == 409
    assert c.delete(url + '/' + playlist['id'], headers={'If-Match': c.get(url).headers['etag']}).status_code == 200
    assert native.rows == [] and context.tracks == [{'id': '1', 'title': 'Track', 'artist': 'A', 'album': 'B'}, {'id': '2', 'title': 'New track'}]
    assert not c.get('/api/engine-busy').json()['busy']


def test_native_unknown_outcome_survives_restart_without_retry(context):
    native = native_context(context)
    def fail(*args):
        native.calls.append('write')
        raise jsymphonic.JSymphonicError('Connection lost', needs_reconcile=True)
    context.api.create_playlist = fail
    c = context.client
    etag = c.get('/api/device/playlists').headers['etag']
    result = c.post('/api/device/playlists', headers={'If-Match': etag}, json={'name': 'Unknown', 'track_ids': ['1']})
    assert result.status_code == 409, result.text
    assert result.json()['detail']['code'] == 'verify_device_state'
    job_id = result.json()['detail']['job_id']
    persisted = JobStore(context.app.state.store.root.parent / 'nightops.sqlite')
    persisted.recover_interrupted()
    job = persisted.get(job_id).to_dict()
    assert job['needs_reconcile'] and job['files'][0]['state'] == 'unknown'
    assert native.calls == ['write']
    assert not c.get('/api/engine-busy').json()['busy']


def test_shim_playlist_contract(monkeypatch):
    calls = []
    def run(args, timeout, on_event=None):
        calls.append(args)
        row = {'event': 'playlist', 'id': '1', 'name': 'Test', 'trackIds': ['3', '2']}
        if args[0] == 'playlists':
            return [row, {'event': 'playlistsEnd', 'count': 1}]
        return ([row] if args[0] != 'playlist-delete' else []) + [{'event': 'done'}]
    monkeypatch.setattr(jsymphonic, '_run', run)
    assert jsymphonic.list_playlists('fixture')[0]['track_ids'] == ['3', '2']
    assert jsymphonic.create_playlist('fixture', 'Test', ['3', '2'])['id'] == '1'
    jsymphonic.update_playlist('fixture', '1', track_ids=[])
    jsymphonic.delete_playlist('fixture', '1')
    assert calls == [
        ['playlists', '--device', 'fixture'],
        ['playlist-create', '--device', 'fixture', '--name-base64', 'VGVzdA==', '3', '2'],
        ['playlist-update', '--device', 'fixture', '--id', '1', '--tracks', ''],
        ['playlist-delete', '--device', 'fixture', '--id', '1'],
    ]


def test_saved_playlist_scope_restores_and_deletion_clears_it(context):
    c = context.client
    imported(context)
    media_id = context.app.state.store.queue()[0]['id']
    playlist = c.post('/api/playlists', json={'name': 'Saved scope', 'media_ids': [media_id]}).json()
    assert c.get('/api/queue').json()['playlists'] == [playlist]
    saved = c.patch('/api/playback-state', json={'media_id': media_id, 'playlist_id': playlist['id'], 'position_seconds': 4}).json()
    assert saved['playlist'] == playlist
    assert c.get('/api/playback-state').json()['playlist_id'] == playlist['id']
    assert c.patch('/api/playback-state', json={'playlist_id': 'a' * 32}).status_code == 400
    assert c.delete('/api/playlists/' + playlist['id'], headers={'If-Match': playlist['etag']}).status_code == 200
    saved = c.get('/api/playback-state').json()
    assert saved['playlist'] is None and saved['playlist_id'] is None
    assert saved['media_id'] == media_id and saved['position_seconds'] == 4


def test_saved_playlist_catalog_and_names_are_bounded(tmp_path):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    for name in ['', '\u202e\x00', 'x' * 121]:
        with pytest.raises(MediaError):
            store.create_playlist(name)
    for number in range(200):
        store.create_playlist('List ' + str(number))
    with pytest.raises(MediaError, match='200'):
        store.create_playlist('Too many')
    assert len(store.playlists()) == 200


def test_metadata_edit_regenerates_transfer_and_preserves_user_tags_on_reanalysis(context, monkeypatch):
    import media_service
    imported(context)
    store = context.app.state.store
    media_id = store.queue()[0]['id']
    supplied = []
    def convert(src, out, kind, maximum, metadata=None):
        supplied.append(metadata)
        out.write_bytes(src.read_bytes() + metadata['title'].encode())
    monkeypatch.setattr(media_service, 'convert_managed', convert)
    c = context.client
    assert c.post('/api/transfers', json={'media_ids': [media_id]}).status_code == 200
    record = store.get(media_id)
    transfer = store.path(record, 'transfer')
    before = transfer.read_bytes()
    updated = c.patch(f'/api/media/{media_id}/metadata', json={'title': 'Saved title', 'year': '2025'}).json()
    assert 'metadata_overrides' not in updated
    assert transfer.read_bytes() == before  # Editing never deletes an active artifact.
    store.update(media_id, metadata_policy=0)
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {'title': 'Embedded title', 'year': 1999})
    second = c.post('/api/transfers', json={'media_ids': [media_id]})
    job = c.get('/api/jobs/' + second.json()['job_id']).json()
    assert job['status'] == 'done', job
    assert len(supplied) == 2 and supplied[-1]['title'] == 'Saved title'
    assert supplied[-1]['year'] == 2025
    assert store.get(media_id)['artifacts']['transfer']['metadata_revision'] == updated['metadata_revision']
    assert transfer.read_bytes() != before


def test_native_concurrent_edits_recheck_etag_inside_device_lock(context):
    native = native_context(context)
    entered, release = threading.Event(), threading.Event()
    original = context.api.create_playlist
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    context.api.create_playlist = slow
    c = context.client
    etag = c.get('/api/device/playlists').headers['etag']
    responses = []
    def create(name):
        responses.append(c.post('/api/device/playlists', headers={'If-Match': etag}, json={'name': name, 'track_ids': ['1']}))
    first = threading.Thread(target=create, args=('First',))
    second = threading.Thread(target=create, args=('Second',))
    first.start()
    try:
        assert entered.wait(5)
        second.start()
    finally:
        release.set()
        first.join(10)
        if second.ident:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(native.calls) == 1
    assert not c.get('/api/engine-busy').json()['busy']


def test_native_volume_swap_is_rejected_before_write(context, monkeypatch):
    import device
    native = native_context(context)
    c = context.client
    etag = c.get('/api/device/playlists').headers['etag']
    def reject(identity):
        raise device.DeviceChangedError('Volume changed')
    monkeypatch.setattr(device, 'revalidate_device', reject)
    response = c.post('/api/device/playlists', headers={'If-Match': etag}, json={'name': 'No write'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'playlist_failed'
    assert native.calls == []
    job = c.get('/api/jobs/' + response.json()['detail']['job_id']).json()
    assert job['files'][0]['state'] == 'failed' and not job['needs_reconcile']
    assert not c.get('/api/engine-busy').json()['busy']


@pytest.mark.parametrize('events', [
    [{'event': 'playlist', 'id': '1', 'name': 'X', 'trackIds': ['1']}],
    [{'event': 'playlist', 'id': '1', 'name': 'X', 'trackIds': ['../1']}, {'event': 'done'}],
    [{'event': 'done'}],
    [{'event': 'fatal', 'message': 'Rejected'}, {'event': 'done'}],
])
def test_native_mutation_requires_unambiguous_terminal_evidence(monkeypatch, events):
    scripted(monkeypatch, events)
    with pytest.raises(jsymphonic.JSymphonicError) as failure:
        jsymphonic.create_playlist('fixture', 'X', ['1'])
    assert failure.value.needs_reconcile
    assert jsymphonic.wait_for_idle(0)


@pytest.mark.parametrize('events', [
    [{'event': 'playlistsEnd', 'count': 1}],
    [{'event': 'playlistsEnd', 'count': True}],
    [{'event': 'playlist', 'id': '../1', 'name': 'X', 'trackIds': []}, {'event': 'playlistsEnd', 'count': 1}],
])
def test_native_list_rejects_invalid_count_and_ids(monkeypatch, events):
    scripted(monkeypatch, events)
    with pytest.raises(jsymphonic.JSymphonicError) as failure:
        jsymphonic.list_playlists('fixture')
    assert not failure.value.needs_reconcile


def test_native_existing_repeated_members_survive_read_and_rename(monkeypatch):
    row = {'event': 'playlist', 'id': '3', 'name': 'Repeat set', 'trackIds': ['1', '1']}
    scripted(monkeypatch, [row, {'event': 'playlistsEnd', 'count': 1}])
    assert jsymphonic.list_playlists('fixture')[0]['track_ids'] == ['1', '1']
    scripted(monkeypatch, [row, {'event': 'done'}])
    assert jsymphonic.update_playlist('fixture', '3', name='Repeat set')['track_ids'] == ['1', '1']


@pytest.mark.parametrize('name', ['a' * 61, '\U0001f319' * 31])
def test_oversized_native_names_reject_before_admission_or_device_write(context, name):
    native = native_context(context)
    c = context.client
    etag = c.get('/api/device/playlists').headers['etag']
    response = c.post('/api/device/playlists', headers={'If-Match': etag}, json={'name': name})
    assert response.status_code == 400
    assert native.calls == [] and context.app.state.jobs.latest() is None


def test_playlist_edits_obey_authentication_and_drain(context):
    native = native_context(context)
    c = context.client
    assert c.post('/api/playlists', headers={'Origin': 'https://hostile.example'}, json={'name': 'Denied'}).status_code == 403
    assert c.patch('/api/media/' + 'a' * 32 + '/metadata', headers={'Origin': 'https://hostile.example'}, json={'title': 'Denied'}).status_code == 403
    assert c.post('/api/device/playlists', headers={'Origin': 'https://hostile.example'}, json={'name': 'Denied'}).status_code == 403
    etag = c.get('/api/device/playlists').headers['etag']
    c.post('/api/shutdown/drain')
    assert c.post('/api/playlists', json={'name': 'Closing'}).status_code == 409
    assert c.post('/api/device/playlists', headers={'If-Match': etag}, json={'name': 'Closing'}).status_code == 409
    assert native.calls == []


def test_disconnect_after_playlist_write_remains_unknown(context, monkeypatch):
    import device
    native_context(context)
    c = context.client
    etag = c.get('/api/device/playlists').headers['etag']
    original = context.api.create_playlist
    def write_then_disconnect(*args):
        result = original(*args)
        def disconnected(identity):
            raise device.DeviceChangedError('Disconnected after write')
        monkeypatch.setattr(device, 'revalidate_device', disconnected)
        return result
    context.api.create_playlist = write_then_disconnect
    response = c.post('/api/device/playlists', headers={'If-Match': etag}, json={'name': 'Uncertain', 'track_ids': ['1']})
    assert response.status_code == 409
    job = c.get('/api/jobs/' + response.json()['detail']['job_id']).json()
    assert job['needs_reconcile'] and job['files'][0]['state'] == 'unknown'
