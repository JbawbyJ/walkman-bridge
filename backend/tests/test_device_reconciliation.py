"""Owner acknowledgement preserves unknown outcomes and never writes a device."""
import json

import pytest

from device import capture_device_identity
from jobs import JobStore
from jsymphonic import DeviceRows, JSymphonicError
from test_api import context, imported

ROUTE = '/api/device/reconciliation'


def uncertain(ctx):
    ctx.api.inspect_playlist_journal = lambda mount: {'state': 'none', 'exists': False, 'covers': {'files': []}}
    ctx.api.list_playlists = lambda mount: []
    def interrupted(*args):
        raise JSymphonicError('Interrupted write', needs_reconcile=True)
    ctx.api.add_tracks = interrupted
    source = imported(ctx)['files'][0]['media_id']
    response = ctx.client.post('/api/transfers', json={'media_ids': [source]})
    job_id = response.json()['job_id']
    return source, job_id


def acknowledge(ctx, job_id, etag):
    return ctx.client.post(ROUTE, headers={'If-Match': etag}, json={'job_id': job_id, 'acknowledge': True})


def test_write_intent_persists_volume_before_engine(context):
    media_id, job_id = uncertain(context)
    row = context.app.state.jobs.get(job_id).to_dict()['files'][0]
    assert row['volume_id'] == capture_device_identity(context.api.find_walkman()).volume_id
    assert row['state'] == 'unknown'


def test_acknowledgement_keeps_unknown_history_and_survives_restart(context):
    media_id, job_id = uncertain(context)
    response = context.client.get(ROUTE)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot['can_acknowledge'] is True
    assert context.app.state.store.get(media_id)['needs_reconcile'] is True
    before_calls = dict(context.calls)
    response = acknowledge(context, job_id, response.headers['etag'])
    assert response.status_code == 200, response.text
    assert response.json()['automatic_retry'] is False
    job = context.app.state.jobs.get(job_id).to_dict()
    assert not job['needs_reconcile']
    assert job['status'] == 'failed'
    assert job['files'][0]['state'] == 'unknown'
    assert job['files'][0]['reconciliation']['outcome'] == 'owner_acknowledged'
    assert not context.app.state.store.get(media_id)['needs_reconcile']
    assert context.calls['add'] == before_calls['add']
    assert context.calls['delete'] == before_calls['delete']
    reopened = JobStore(context.app.state.jobs._db_path)
    try:
        reopened.recover_interrupted()
        assert media_id not in reopened.uncertain_media_ids()
        assert not reopened.get(job_id).needs_reconcile
    finally:
        reopened.close()


def test_requires_explicit_acknowledgement_and_fresh_snapshot(context):
    media_id, job_id = uncertain(context)
    assert context.client.post(ROUTE, json={'job_id': job_id, 'acknowledge': True}).status_code == 428
    response = context.client.get(ROUTE)
    assert context.client.post(ROUTE, headers={'If-Match': response.headers['etag']},
        json={'job_id': job_id, 'acknowledge': False}).status_code == 422
    context.tracks.append({'id': '2', 'title': 'Changed'})
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409
    assert context.app.state.store.get(media_id)['needs_reconcile']


@pytest.mark.parametrize('journal', [
    {'state': 'committed', 'exists': True, 'covers': {'files': ['01TREE22.DAT']}},
    {'state': None, 'exists': None, 'covers': None},
    {'state': 'none', 'exists': False, 'covers': {'files': []}, 'warnings': [{'code': 'DEVICE_PROBE_PENDING'}]},
])
def test_pending_or_unknown_journal_cannot_unlock(context, journal):
    media_id, job_id = uncertain(context)
    context.api.inspect_playlist_journal = lambda mount: journal
    response = context.client.get(ROUTE)
    assert response.status_code == 200
    assert response.json()['can_acknowledge'] is False
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409
    assert context.app.state.store.get(media_id)['needs_reconcile']


def test_probe_warning_in_inventory_prevents_acknowledgement(context):
    _, job_id = uncertain(context)
    context.api.list_tracks = lambda mount: DeviceRows(context.tracks, [{'code': 'DEVICE_PROBE_PENDING'}])
    response = context.client.get(ROUTE)
    assert response.json()['can_acknowledge'] is False
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409


def test_reconnected_different_device_cannot_clear_original_job(context, tmp_path):
    media_id, job_id = uncertain(context)
    other = tmp_path / 'other'
    (other / 'OMGAUDIO').mkdir(parents=True)
    context.api.find_walkman = lambda: other
    response = context.client.get(ROUTE)
    assert response.status_code == 200
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409
    assert context.app.state.store.get(media_id)['needs_reconcile']


def test_legacy_unbound_job_remains_blocked(context):
    media_id, job_id = uncertain(context)
    job = context.app.state.jobs.get(job_id)
    rows = job.to_dict()['files']
    rows[0].pop('volume_id', None)
    job.set_files(rows)
    response = context.client.get(ROUTE)
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409
    assert context.app.state.store.get(media_id)['needs_reconcile']


def test_other_uncertain_job_keeps_shared_media_blocked(context):
    media_id, job_id = uncertain(context)
    jobs = context.app.state.jobs
    second = jobs.create('other-uncertain', 1, kind='transfer')
    second.needs_reconcile = True
    second.set_files(jobs.get(job_id).to_dict()['files'])
    second.set_status('failed', 'Other interrupted write')
    response = context.client.get(ROUTE)
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 200
    assert context.app.state.store.get(media_id)['needs_reconcile']
    assert second.needs_reconcile


def test_sqlite_failure_rolls_back_job_and_media_together(context):
    media_id, job_id = uncertain(context)
    jobs = context.app.state.jobs
    jobs._conn.execute("CREATE TRIGGER reject_reconcile BEFORE UPDATE ON media BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    jobs._conn.commit()
    response = context.client.get(ROUTE)
    response = acknowledge(context, job_id, response.headers['etag'])
    assert response.status_code == 409
    assert jobs.get(job_id).needs_reconcile
    assert context.app.state.store.get(media_id)['needs_reconcile']
    persisted = jobs._conn.execute('SELECT needs_reconcile FROM jobs WHERE job_id=?', (job_id,)).fetchone()
    assert persisted[0] == 1


def test_reconciliation_is_authenticated(context):
    uncertain(context)
    context.client.headers.pop('X-NightOps-Token')
    assert context.client.get(ROUTE).status_code == 403


def test_dangling_playlist_exposes_repair_without_unlocking(context):
    _, job_id = uncertain(context)
    def dangling(mount):
        raise JSymphonicError('Missing playlist member', code='PLAYLIST_REF_MISSING')
    context.api.list_playlists = dangling
    response = context.client.get(ROUTE)
    assert response.status_code == 200
    assert response.json()['inventory_error']['fatal_code'] == 'PLAYLIST_REF_MISSING'
    assert not response.json()['can_acknowledge']
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409


def test_recovery_confirmation_refuses_replaced_volume(context, tmp_path):
    uncertain(context)
    calls = []
    context.api.recover_playlist_journal = lambda mount: calls.append(mount) or {'outcome': 'none'}
    inspected = context.client.get('/api/device/playlist-recovery/inspect')
    other = tmp_path / 'replacement'
    (other / 'OMGAUDIO').mkdir(parents=True)
    context.api.find_walkman = lambda: other
    response = context.client.post('/api/device/playlist-recovery/recover', headers={'If-Match': inspected.headers['etag']})
    assert response.status_code == 409
    assert not calls


def test_admitted_device_write_blocks_acknowledgement(context):
    _, job_id = uncertain(context)
    snapshot = context.client.get(ROUTE)
    coordinator = context.app.state.coordinator
    ticket = coordinator.admit('transfer', capture_device_identity(context.api.find_walkman()))
    try:
        assert acknowledge(context, job_id, snapshot.headers['etag']).status_code == 409
    finally:
        coordinator.finish(ticket)


def test_disconnect_on_final_session_check_keeps_both_retry_gates(context, monkeypatch):
    import device
    media_id, job_id = uncertain(context)
    response = context.client.get(ROUTE)
    original = device.revalidate_device
    calls = []
    def changed_at_exit(volume):
        calls.append(volume)
        if len(calls) == 2:
            raise device.DeviceChangedError('Disconnected during verification')
        return original(volume)
    monkeypatch.setattr(device, 'revalidate_device', changed_at_exit)
    assert acknowledge(context, job_id, response.headers['etag']).status_code == 409
    assert context.app.state.jobs.get(job_id).needs_reconcile
    assert context.app.state.store.get(media_id)['needs_reconcile']
