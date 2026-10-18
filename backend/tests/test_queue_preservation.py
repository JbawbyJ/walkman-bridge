"""Queue actions must never delete the managed library; all files are synthetic."""
import io
import sqlite3
import pytest

from media_store import MediaStore
from test_api import context, imported


@pytest.mark.parametrize('leased', [False, True])
def test_queue_removal_preserves_library_and_playlists_after_stop_and_restart(context, leased):
    c, store = context.client, context.app.state.store
    imported(context, ('keep.wav', 'next.wav'))
    first, second = store.queue()
    media_id = first['id']
    source = store.path(first)
    before = source.read_bytes()
    playlist = c.post('/api/playlists', json={'name': 'Keep', 'media_ids': [media_id, second['id']]}).json()
    if leased:
        assert c.post('/api/playback/lease', json={'media_id': media_id}).status_code == 200
    assert c.delete(f'/api/queue/items/{media_id}').status_code == 200
    assert [r['id'] for r in c.get('/api/queue').json()['items']] == [second['id']]
    if leased:
        assert c.request('DELETE', '/api/playback/lease', json={'media_id': media_id}).status_code == 200
    assert source.exists(), 'Removing a queue entry deleted managed music'
    assert source.read_bytes() == before
    assert c.get('/api/playlists/' + playlist['id']).json() == playlist
    assert [r['id'] for r in c.get('/api/queue').json()['library_items']] == [media_id, second['id']]
    assert c.get(f'/api/media/{media_id}/stream').content == before
    # Repeated queue removal remains harmless, including after leases are gone.
    assert c.delete(f'/api/queue/items/{media_id}').status_code == 200
    reopened = MediaStore(store.root.parent / 'nightops.sqlite', store.root)
    try:
        assert [r['id'] for r in reopened.queue()] == [second['id']]
        assert reopened.path(reopened.get(media_id)).read_bytes() == before
        assert reopened.get_playlist(playlist['id']) == playlist
        # Hidden queue entries remain valid editable playlist members.
        assert reopened.update_playlist(playlist['id'], {'name': 'Still here'}, playlist['etag'])['media_ids'] == playlist['media_ids']
    finally:
        reopened._db.close()
    assert context.calls['delete'] == context.calls['add'] == 0


def test_legacy_database_migration_preserves_queue_and_pending_library_deletion(tmp_path):
    root, db = tmp_path / 'cache', tmp_path / 'state.db'
    store = MediaStore(db, root)
    keep = store.import_file('keep.wav', io.BytesIO(b'keep'))
    pending = store.import_file('pending.wav', io.BytesIO(b'pending'))
    keep_path, pending_path = store.path(keep), store.path(pending)
    store._db.close()
    # Recreate the pre-separation schema, including a deferred deletion from
    # an older process that ended before releasing its playback lease.
    with sqlite3.connect(db) as legacy:
        legacy.execute('CREATE TABLE old_media(id TEXT PRIMARY KEY, record TEXT NOT NULL, position INTEGER)')
        legacy.execute('INSERT INTO old_media SELECT id,record,position FROM media')
        legacy.execute('UPDATE old_media SET position=NULL WHERE id=?', (pending['id'],))
        legacy.execute('DROP TABLE media')
        legacy.execute('ALTER TABLE old_media RENAME TO media')
    reopened = MediaStore(db, root)
    try:
        assert [r['id'] for r in reopened.queue()] == [keep['id']]
        assert keep_path.read_bytes() == b'keep'
        assert not pending_path.exists()
        reopened.remove_from_queue(keep['id'])
    finally:
        reopened._db.close()
    again = MediaStore(db, root)
    try:
        assert again.queue() == []
        assert again.path(again.get(keep['id'])).read_bytes() == b'keep'
    finally:
        again._db.close()


def test_explicit_library_delete_still_prunes_hidden_track_and_waits_for_lease(context):
    c, store = context.client, context.app.state.store
    media_id = imported(context)['files'][0]['media_id']
    source = store.path(store.get(media_id))
    playlist = c.post('/api/playlists', json={'name': 'Set', 'media_ids': [media_id]}).json()
    assert c.delete(f'/api/queue/items/{media_id}').status_code == 200
    with store.lease(media_id):
        assert c.delete(f'/api/media/{media_id}').status_code == 200
        assert source.exists()
        assert c.get('/api/queue').json()['library_items'] == []
        assert c.get('/api/playlists/' + playlist['id']).json()['media_ids'] == []
    assert not source.exists()
    assert context.calls['delete'] == context.calls['add'] == 0
