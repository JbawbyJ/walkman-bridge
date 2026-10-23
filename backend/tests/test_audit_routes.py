"""Route regressions: renderer-safe reconciliation records and tickets that always finish."""
import json
import sqlite3

import pytest

from jobs import JobStore
from jsymphonic import JSymphonicError
from test_api import context, imported

ROUTE = '/api/device/reconciliation'
PUBLIC_SCAN_KEYS = {'ok', 'state', 'reason', 'reason_code', 'defender_status'}


@pytest.fixture
def ctx(context):
    # A leaked ticket would hang lifespan drain forever; bound it so a regression fails instead.
    coordinator = context.app.state.coordinator
    wait = coordinator.wait_drained
    coordinator.wait_drained = lambda timeout=None: wait(timeout=5)
    return context


def elevated_scan(ctx):
    root = ctx.app.state.store.root
    return dict(ok=True, state='CLEAN', reason='Clean', reason_code='clean', defender_status='clean',
                nonce='n' * 32, request_id='req-1234', helper_pid=4242, media_id='m' * 32,
                path=str(root / ('a' * 32) / 'source.wav'), sha256='f' * 64, size_bytes=15, exit_code=0,
                scanned_at=1_790_000_000.0, defender_engine_version='1.1.24090.11',
                defender_signature_version='1.419.1.0', defender_platform_version='4.18.24090.11')


def uncertain_transfer(ctx):
    ctx.api.inspect_playlist_journal = lambda mount: {'state': 'none', 'exists': False, 'covers': {'files': []},
                                                      'guarded': True, 'library_fingerprint': 'a' * 64}
    ctx.api.list_playlists = lambda mount: []
    def interrupted(*args):
        raise JSymphonicError('Interrupted write', needs_reconcile=True)
    ctx.api.add_tracks = interrupted
    media_id = imported(ctx)['files'][0]['media_id']
    response = ctx.client.post('/api/transfers', json={'media_ids': [media_id]})
    assert response.status_code == 200, response.text
    return media_id, response.json()['job_id']


def test_reconciliation_snapshot_projects_scan_records(ctx):
    media_id, job_id = uncertain_transfer(ctx)
    full = elevated_scan(ctx)
    ctx.app.state.jobs.get(job_id).update_file(media_id, scan=full)
    response = ctx.client.get(ROUTE)
    assert response.status_code == 200, response.text
    job = next(j for j in response.json()['jobs'] if j['job_id'] == job_id)
    assert job['files'][0]['scan'] == {key: full[key] for key in PUBLIC_SCAN_KEYS}
    for secret in (full['nonce'], full['request_id'], full['path'], full['sha256'], str(ctx.app.state.store.root)):
        # Compare JSON-escaped text so Windows backslashes in paths are matched too.
        assert json.dumps(secret)[1:-1] not in response.text
    # Inspect and acknowledgement project identically, so the ETag still matches.
    again = ctx.client.get(ROUTE)
    assert again.headers['etag'] == response.headers['etag']
    ack = ctx.client.post(ROUTE, headers={'If-Match': response.headers['etag']},
                          json={'job_id': job_id, 'acknowledge': True})
    assert ack.status_code == 200, ack.text
    # The backend keeps the complete record; only the renderer copy is projected.
    assert ctx.app.state.jobs.get(job_id).to_dict()['files'][0]['scan'] == full
    reopened = JobStore(ctx.app.state.jobs._db_path)
    try:
        assert reopened.get(job_id).files[0]['scan'] == full
    finally:
        reopened.close()


def test_delete_releases_ticket_when_job_cannot_be_recorded(ctx):
    etag = ctx.client.get('/api/tracks').headers['etag']
    def disk_failure(*args, **kwargs):
        raise sqlite3.OperationalError('disk I/O error')
    # Scoped patch: undoing the fixture's monkeypatch would also drop its fake converter.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ctx.app.state.jobs, 'create', disk_failure)
        response = ctx.client.delete('/api/tracks/1', headers={'If-Match': etag})
    assert 400 <= response.status_code < 500, response.text
    assert response.json()['detail']['code'] == 'delete_failed'
    assert ctx.app.state.coordinator.snapshot()['busy'] is False
    assert ctx.calls['delete'] == 0
    retry = ctx.client.delete('/api/tracks/1', headers={'If-Match': etag})
    assert retry.status_code == 200, retry.text
    assert ctx.calls['delete'] == 1


def test_failed_playback_cleanup_still_finishes_session(ctx):
    media_id = imported(ctx)['files'][0]['media_id']
    store = ctx.app.state.store
    assert ctx.client.post('/api/playback/lease', json={'media_id': media_id}).status_code == 200
    assert ctx.client.delete(f'/api/media/{media_id}').status_code == 200
    def locked(media_id):
        raise PermissionError('The process cannot access the file because it is being used by another process')
    with pytest.MonkeyPatch.context() as patch, pytest.raises(PermissionError):
        patch.setattr(store, '_cleanup', locked)
        ctx.client.request('DELETE', '/api/playback/lease', json={'media_id': media_id})
    assert ctx.app.state.coordinator.snapshot()['busy'] is False
    assert ctx.client.post('/api/shutdown/drain').json()['busy'] is False


def test_failed_import_cleanup_releases_import_slot(ctx):
    store, service = ctx.app.state.store, ctx.app.state.service
    def unrecordable(*args):
        raise sqlite3.OperationalError('disk I/O error')
    remove, attempted = store.remove, []
    def remove_once_locked(media_id):
        attempted.append(media_id)
        if len(attempted) == 1:
            raise PermissionError('The process cannot access the file because it is being used by another process')
        remove(media_id)
    files = [('files', (name, b'RIFF audio test', 'audio/wav')) for name in ('a.wav', 'b.wav')]
    # The caller sees the original failure, not the cleanup error.
    with pytest.MonkeyPatch.context() as patch, pytest.raises(sqlite3.OperationalError):
        patch.setattr(service, 'create_job', unrecordable)
        patch.setattr(store, 'remove', remove_once_locked)
        ctx.client.post('/api/media/import', files=files)
    assert len(attempted) == 2
    assert [row['id'] for row in store.queue()] == [attempted[0]]
    assert ctx.app.state.coordinator.snapshot()['busy'] is False
    assert imported(ctx)['status'] == 'done'


def test_failed_schedule_cleanup_still_finishes_ticket(ctx, monkeypatch):
    media_id = imported(ctx)['files'][0]['media_id']
    service = ctx.app.state.service
    hold = service.hold
    def failing_release(ids):
        leases = hold(ids)
        def locked():
            raise PermissionError('The process cannot access the file because it is being used by another process')
        leases.callback(locked)
        return leases
    def unrecordable(*args):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(service, 'hold', failing_release)
    monkeypatch.setattr(service, 'create_job', unrecordable)
    with pytest.raises(Exception):
        ctx.client.post(f'/api/media/{media_id}/rescan')
    assert ctx.app.state.coordinator.snapshot()['busy'] is False


def test_failed_switch_cleanup_still_finishes_both_sessions(ctx):
    first = imported(ctx)['files'][0]['media_id']
    second = imported(ctx)['files'][0]['media_id']
    store = ctx.app.state.store
    assert ctx.client.post('/api/playback/lease', json={'media_id': first}).status_code == 200
    assert ctx.client.delete(f'/api/media/{first}').status_code == 200
    def locked(media_id):
        raise PermissionError('The process cannot access the file because it is being used by another process')
    # Releasing the old track fails, then so does dropping the new lease on the way out.
    with pytest.MonkeyPatch.context() as patch, pytest.raises(PermissionError):
        patch.setattr(store, '_cleanup', locked)
        ctx.client.post('/api/playback/lease', json={'media_id': second})
    assert ctx.app.state.coordinator.snapshot()['busy'] is False
