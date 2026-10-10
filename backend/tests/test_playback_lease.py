from test_api import context, imported


def test_buffered_playback_retains_removed_copy_until_stop_and_drain(context):
    media_id = imported(context)['files'][0]['media_id']
    store = context.app.state.store
    source = store.path(store.get(media_id))
    assert context.client.post('/api/playback/lease', json={'media_id': media_id}).status_code == 200
    assert context.client.get(f'/api/media/{media_id}/stream').status_code == 200
    assert context.client.delete(f'/api/queue/items/{media_id}').status_code == 200
    assert source.exists()
    assert context.client.post('/api/shutdown/drain').json()['busy'] is True
    assert context.client.request('DELETE', '/api/playback/lease', json={'media_id': media_id}).status_code == 200
    assert not source.exists()
    assert context.client.get('/api/engine-busy').json()['busy'] is False


def test_stale_release_cannot_drop_new_track_lease(context):
    first = imported(context)['files'][0]['media_id']
    second = imported(context)['files'][0]['media_id']
    context.client.post('/api/playback/lease', json={'media_id': first})
    context.client.post('/api/playback/lease', json={'media_id': second})
    context.client.request('DELETE', '/api/playback/lease', json={'media_id': first})
    assert context.client.get('/api/engine-busy').json()['busy'] is True
    context.client.request('DELETE', '/api/playback/lease', json={'media_id': second})
    assert context.client.get('/api/engine-busy').json()['busy'] is False
