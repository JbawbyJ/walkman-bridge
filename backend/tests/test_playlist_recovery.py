"""Playlist recovery endpoints driven by the fake shim (SHIM_CMD_PREFIX).

Present `state` / `outcome` values are passed through from `playlistJournal`.
Missing and unknown values stay null. Message text is never a substitute.
Repair summary ids are JSON numbers and come back as strings. A final `done`
event is required.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import jsymphonic
from jsymphonic import JSymphonicError
from main import create_app
from test_api import ORIGIN, Scanner, TOKEN, context
from test_jsymphonic import scripted

INSPECT = '/api/device/playlist-recovery/inspect'
RECOVER = '/api/device/playlist-recovery/recover'
REPAIR = '/api/device/playlist-recovery/repair'
FAKE_SHIM = Path(__file__).parent / 'fake_shim.py'
CODES = (
    'PLAYLIST_REF_MISSING',
    'PLAYLIST_JOURNAL_PENDING',
    'PLAYLIST_SLOTS_EXHAUSTED',
    'DEVICE_ROLLBACK_FAILED',
)
RECOVERY_BY_CODE = {
    'PLAYLIST_REF_MISSING': 'repair',
    'PLAYLIST_JOURNAL_PENDING': 'inspect_recover',
    'PLAYLIST_SLOTS_EXHAUSTED': 'free_slots',
    'PLAYLIST_LIBRARY_NOT_LOADED': 'reconnect_retry',
    'DEVICE_ROLLBACK_FAILED': 'inspect_recover',
    'DEVICE_FILE_LOCKED': 'close_and_retry',
    'DEVICE_FILE_READ_ONLY': 'clear_read_only_retry',
}


def use_fake(monkeypatch, context, scenario):
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [sys.executable, str(FAKE_SHIM), scenario])
    context.api.inspect_playlist_journal = jsymphonic.inspect_playlist_journal
    context.api.recover_playlist_journal = jsymphonic.recover_playlist_journal
    context.api.repair_playlists = jsymphonic.repair_playlists


def bind_script(context, monkeypatch, events, exit_code=0):
    scripted(monkeypatch, events, exit_code)
    context.api.inspect_playlist_journal = jsymphonic.inspect_playlist_journal
    context.api.recover_playlist_journal = jsymphonic.recover_playlist_journal
    context.api.repair_playlists = jsymphonic.repair_playlists


@pytest.mark.parametrize('scenario, state, exists', [
    ('playlist_inspect_committed', 'committed', True),
    ('playlist_inspect_uncommitted', 'uncommitted', True),
    ('playlist_inspect_none', 'none', False),
    ('playlist_inspect_missing', None, None),
    ('playlist_inspect_unknown', None, None),
])
def test_inspect_state_comes_only_from_the_state_field(context, monkeypatch, scenario, state, exists):
    use_fake(monkeypatch, context, scenario)
    response = context.client.get(INSPECT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['state'] == state
    assert body['exists'] is exists
    assert context.app.state.jobs.latest() is None


def test_inspect_covers_come_from_journal_files(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_inspect_committed')
    body = context.client.get(INSPECT).json()
    assert body['state'] == 'committed'
    assert body['covers'] == {'files': ['01TREE22.DAT', '10F00/10000001.OMA', 'tree', 'info']}
    assert 'playlist_ids' not in body['covers']
    assert 'track_ids' not in body['covers']


@pytest.mark.parametrize('scenario', ['playlist_inspect_missing', 'playlist_inspect_uncommitted', 'playlist_inspect_unknown'])
def test_inspect_without_a_string_file_list_has_null_covers(context, monkeypatch, scenario):
    use_fake(monkeypatch, context, scenario)
    assert context.client.get(INSPECT).json()['covers'] is None


def test_inspect_none_passes_an_empty_file_list(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_inspect_none')
    body = context.client.get(INSPECT).json()
    assert body['state'] == 'none'
    assert body['covers'] == {'files': []}


def test_step_state_does_not_hide_a_journal_state(monkeypatch):
    scripted(monkeypatch, [
        {'event': 'step', 'state': 'finished', 'message': 'uncommitted'},
        {'event': 'playlistJournal', 'state': 'committed', 'files': ['01TREE22.DAT', '10F00/10000001.OMA']},
        {'event': 'done', 'state': 'uncommitted', 'outcome': 'discarded', 'files': ['ignored.mp3']},
    ])
    body = jsymphonic.inspect_playlist_journal('fixture')
    assert body['state'] == 'committed'
    assert body['covers'] == {'files': ['01TREE22.DAT', '10F00/10000001.OMA']}


@pytest.mark.parametrize('scenario, outcome', [
    ('playlist_recover_rolled_forward', 'rolled_forward'),
    ('playlist_recover_discarded', 'discarded'),
    ('playlist_recover_none', 'none'),
    ('playlist_recover_missing', None),
    ('playlist_recover_unknown', None),
])
def test_recover_outcome_comes_only_from_the_outcome_field(context, monkeypatch, scenario, outcome):
    use_fake(monkeypatch, context, scenario)
    response = context.client.post(RECOVER)
    assert response.status_code == 200, response.text
    assert response.json()['outcome'] == outcome
    assert response.json()['ok'] is True


def test_repair_zero_pruned_is_visible(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_zero')
    response = context.client.post(REPAIR)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is True
    assert body['pruned_count'] == 0
    assert body['pruned_track_ids'] == []
    assert body['playlist_ids'] == []
    assert 'empty_mount' not in body


def test_repair_normalizes_numeric_summary_ids(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_some')
    body = context.client.post(REPAIR).json()
    assert body['pruned_count'] == 2
    assert body['pruned_track_ids'] == ['3']
    assert body['playlist_ids'] == ['1']
    assert body['pruned_count'] > len(body['pruned_track_ids'])


def test_repair_missing_count_stays_unknown(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_missing')
    body = context.client.post(REPAIR).json()
    assert body['ok'] is True
    assert body['pruned_count'] is None
    assert body['pruned_track_ids'] is None
    assert body['playlist_ids'] is None


def test_repair_empty_mount_fatal_is_library_not_loaded(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_empty_mount')
    assert context.client.get('/api/tracks').status_code == 200
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'library_not_loaded'
    assert detail['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'
    assert detail['recovery_action'] == 'reconnect_retry'
    assert 'empty_mount' not in detail
    assert 'fatal_path' not in detail
    assert 'Playlist repair refused' in detail['message']
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'
    assert 'empty_mount' not in job['files'][0]


def test_repair_refuses_an_empty_track_list_before_the_shim(context, monkeypatch):
    context.tracks.clear()
    use_fake(monkeypatch, context, 'playlist_repair_some')
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'library_not_loaded'
    assert detail['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'
    assert detail['recovery_action'] == 'reconnect_retry'
    assert 'empty_mount' not in detail
    assert context.calls['list'] == 1
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'


def test_repair_lists_again_when_the_cache_is_stale(context, monkeypatch):
    assert context.client.get('/api/tracks').json()
    assert context.calls['list'] == 1
    context.tracks.clear()
    repaired = []
    context.api.repair_playlists = lambda mount: repaired.append(mount)
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'library_not_loaded'
    assert detail['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'
    assert detail['recovery_action'] == 'reconnect_retry'
    assert repaired == []
    assert context.calls['list'] == 2
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False


def test_repair_refreshes_a_cached_empty_list_before_refusing(context, monkeypatch):
    context.tracks.clear()
    assert context.client.get('/api/tracks').json() == []
    use_fake(monkeypatch, context, 'playlist_repair_zero')
    context.tracks.append({'id': '1', 'title': 'Track', 'artist': 'A', 'album': 'B'})
    response = context.client.post(REPAIR)
    assert response.status_code == 200, response.text
    assert context.calls['list'] == 2


def test_repair_surfaces_a_listing_failure_and_does_not_repair(context):
    def fail(mount):
        raise jsymphonic.JSymphonicError('device list failed', code='PLAYLIST_JOURNAL_PENDING')

    context.api.list_tracks = fail
    repaired = []
    context.api.repair_playlists = lambda mount: repaired.append(mount)
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['message'] == 'device list failed'
    assert detail['fatal_code'] == 'PLAYLIST_JOURNAL_PENDING'
    assert detail['recovery_action'] == 'inspect_recover'
    assert repaired == []
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False


def test_inspect_is_read_only_and_keeps_the_track_cache(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_inspect_none')
    client = context.client
    assert client.get('/api/tracks').status_code == 200
    assert context.calls['list'] == 1
    assert client.get(INSPECT).status_code == 200
    assert client.get('/api/tracks').status_code == 200
    assert context.calls['list'] == 1
    assert context.app.state.jobs.latest() is None
    assert client.get('/api/engine-busy').json()['busy'] is False


@pytest.mark.parametrize('scenario, path', [
    ('playlist_recover_none', RECOVER),
    ('playlist_repair_zero', REPAIR),
])
def test_successful_mutation_clears_the_track_cache(context, monkeypatch, scenario, path):
    use_fake(monkeypatch, context, scenario)
    client = context.client
    assert client.get('/api/tracks').status_code == 200
    assert context.calls['list'] == 1
    result = client.post(path)
    assert result.status_code == 200, result.text
    job = client.get('/api/jobs/' + result.json()['job_id']).json()
    assert job['status'] == 'done' and job['needs_reconcile'] is False
    assert client.get('/api/tracks').status_code == 200
    assert context.calls['list'] == (3 if path == REPAIR else 2)


def test_inspect_fatal_is_not_mutating(monkeypatch):
    scripted(monkeypatch, [{'event': 'fatal', 'message': 'journal open', 'code': 'PLAYLIST_JOURNAL_PENDING'}], 1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.inspect_playlist_journal('fixture')
    assert error.value.needs_reconcile is False
    assert error.value.code == 'PLAYLIST_JOURNAL_PENDING'
    assert error.value.path is None


@pytest.mark.parametrize('command', ['recover_playlist_journal', 'repair_playlists'])
def test_mutation_fatal_needs_reconcile(monkeypatch, command):
    scripted(monkeypatch, [{'event': 'fatal', 'message': 'blocked', 'code': 'PLAYLIST_REF_MISSING'}], 1)
    with pytest.raises(JSymphonicError) as error:
        getattr(jsymphonic, command)('fixture')
    assert error.value.needs_reconcile is True
    assert error.value.code == 'PLAYLIST_REF_MISSING'


@pytest.mark.parametrize('code', [*CODES, None])
def test_inspect_passes_fatal_code_like_a_read(context, monkeypatch, code):
    event = {'event': 'fatal', 'message': 'blocked PLAYLIST_JOURNAL_PENDING'}
    if code is not None:
        event['code'] = code
    bind_script(context, monkeypatch, [event], 1)
    response = context.client.get(INSPECT)
    assert response.status_code == 409, response.text
    if code is None:
        assert response.json()['detail'] == 'blocked PLAYLIST_JOURNAL_PENDING'
    else:
        assert response.json()['detail'] == {
            'message': 'blocked PLAYLIST_JOURNAL_PENDING',
            'fatal_code': code,
            'recovery_action': RECOVERY_BY_CODE[code],
        }
    assert context.app.state.jobs.latest() is None


@pytest.mark.parametrize('path', [RECOVER, REPAIR])
@pytest.mark.parametrize('code', [*CODES, None, 'PLAYLIST_OTHER'])
def test_mutation_passes_fatal_code_on_the_write_error(context, monkeypatch, path, code):
    event = {'event': 'fatal', 'message': 'see PLAYLIST_REF_MISSING', 'details': {'code': 'PLAYLIST_SLOTS_EXHAUSTED'}}
    if code is not None:
        event['code'] = code
    bind_script(context, monkeypatch, [event], 1)
    response = context.client.post(path)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'verify_device_state'
    assert detail['fatal_code'] == (code if code in CODES else None)
    assert detail['recovery_action'] == RECOVERY_BY_CODE.get(detail['fatal_code'])
    assert 'Verify device state' in detail['message']
    assert 'empty_mount' not in detail
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is True
    assert job['files'][0]['fatal_code'] == detail['fatal_code']
    assert job['files'][0]['state'] == 'unknown'


def test_nested_code_is_not_a_fatal_code(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_fatal_nested_code')
    response = context.client.get(INSPECT)
    assert response.status_code == 409
    assert response.json()['detail'] == 'PLAYLIST_REF_MISSING'


def test_commands_use_device_flag_and_inspect_is_the_read_switch(monkeypatch, tmp_path):
    record = tmp_path / 'argv.json'
    code = (
        'import json,sys,pathlib; '
        f'pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:])); '
        'print(json.dumps({"event":"done"}), flush=True)'
    )
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [sys.executable, '-c', code])
    mount = tmp_path / 'mount'
    jsymphonic.inspect_playlist_journal(mount)
    assert json.loads(record.read_text()) == ['playlist-recover', '--device', str(mount), '--inspect']
    jsymphonic.recover_playlist_journal(mount)
    assert json.loads(record.read_text()) == ['playlist-recover', '--device', str(mount)]
    jsymphonic.repair_playlists(mount)
    assert json.loads(record.read_text()) == ['playlist-repair', '--device', str(mount)]


LOCKED_FATAL_LINE = '{"event":"fatal","message":"Device file is locked","code":"DEVICE_FILE_LOCKED","path":"OMGAUDIO/10F00/10000001.OMA"}'
ROLLBACK_FATAL_LINE = '{"event":"fatal","message":"Database update failed; incomplete recovery requires a verified backup","code":"DEVICE_ROLLBACK_FAILED"}'
READ_ONLY_FATAL_LINE = '{"event":"fatal","message":"Device file is read-only","code":"DEVICE_FILE_READ_ONLY","path":"OMGAUDIO/10F00/10000001.OMA"}'
PROBE_RESTORE_FATAL_LINE = '{"event":"fatal","message":"Device file probe could not be restored","code":"DEVICE_PROBE_RESTORE_FAILED","path":"OMGAUDIO/10F00/10000001.OMA","probe_path":"OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe"}'
PROBE_CONFLICT_FATAL_LINE = '{"event":"fatal","message":"Device file probe conflicts with the track","code":"DEVICE_PROBE_CONFLICT","path":"OMGAUDIO/10F00/10000001.OMA","probe_path":"OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe"}'
PROBE_PENDING_WARNING_LINE = '{"event":"warning","code":"DEVICE_PROBE_PENDING","path":"OMGAUDIO/10F00/10000001.OMA","probe_path":"OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe"}'
MARKUP_PROBE_PATH = 'OMGAUDIO/<b>.jsymphonic-probe'


def test_confirmed_fatal_lines_are_emitted_whole():
    for scenario, line in (
        ('playlist_file_locked', LOCKED_FATAL_LINE),
        ('playlist_file_read_only', READ_ONLY_FATAL_LINE),
        ('playlist_rollback_failed', ROLLBACK_FATAL_LINE),
        ('playlist_probe_restore_failed', PROBE_RESTORE_FATAL_LINE),
        ('playlist_probe_conflict', PROBE_CONFLICT_FATAL_LINE),
    ):
        result = subprocess.run([sys.executable, str(FAKE_SHIM), scenario], capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert result.stdout == line + '\n'
        assert result.stderr == ''


def test_probe_pending_warning_line_is_emitted_whole():
    result = subprocess.run([sys.executable, str(FAKE_SHIM), 'device_probe_pending'], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout == PROBE_PENDING_WARNING_LINE + '\n'
    assert result.stderr == ''
    event = json.loads(result.stdout)
    assert 'message' not in event


@pytest.mark.parametrize('path, scenario, reconcile, action, detail_code, file_state', [
    (RECOVER, 'playlist_file_locked', True, 'inspect_recover', 'verify_device_state', 'unknown'),
    (REPAIR, 'playlist_file_locked', False, 'close_and_retry', 'playlist_failed', 'failed'),
    (RECOVER, 'playlist_file_read_only', True, 'inspect_recover', 'verify_device_state', 'unknown'),
    (REPAIR, 'playlist_file_read_only', False, 'clear_read_only_retry', 'playlist_failed', 'failed'),
])
def test_file_fatal_action_comes_from_the_endpoint(context, monkeypatch, path, scenario, reconcile, action, detail_code, file_state):
    use_fake(monkeypatch, context, scenario)
    response = context.client.post(path)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    if scenario == 'playlist_file_locked':
        event = json.loads(LOCKED_FATAL_LINE)
        assert detail['fatal_code'] == event['code']
        assert detail['fatal_path'] == event['path']
    else:
        assert detail['fatal_code'] == 'DEVICE_FILE_READ_ONLY'
        assert detail['fatal_path'] == 'OMGAUDIO/10F00/10000001.OMA'
    assert detail['recovery_action'] == action
    assert detail['code'] == detail_code
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is reconcile
    assert job['files'][0]['state'] == file_state
    assert job['files'][0]['fatal_code'] == detail['fatal_code']
    assert job['files'][0]['fatal_path'] == detail['fatal_path']


def test_locked_action_ignores_message_text(context, monkeypatch):
    committed = {
        'event': 'fatal',
        'message': 'roll-forward already committed; journal still pending',
        'code': 'DEVICE_FILE_LOCKED',
        'path': 'OMGAUDIO/10F00/10000001.OMA',
    }
    bind_script(context, monkeypatch, [committed], 1)
    repair = context.client.post(REPAIR)
    assert repair.status_code == 409, repair.text
    repair_detail = repair.json()['detail']
    assert repair_detail['recovery_action'] == 'close_and_retry'
    assert repair_detail['fatal_path'] == committed['path']
    repair_job = context.client.get('/api/jobs/' + repair_detail['job_id']).json()
    assert repair_job['needs_reconcile'] is False
    untouched = {
        'event': 'fatal',
        'message': 'Nothing was written and no journal is left',
        'code': 'DEVICE_FILE_LOCKED',
        'path': 'OMGAUDIO/10F00/10000001.OMA',
    }
    bind_script(context, monkeypatch, [untouched], 1)
    recover = context.client.post(RECOVER)
    assert recover.status_code == 409, recover.text
    recover_detail = recover.json()['detail']
    assert recover_detail['fatal_code'] == 'DEVICE_FILE_LOCKED'
    assert recover_detail['recovery_action'] == 'inspect_recover'
    assert recover_detail['fatal_path'] == untouched['path']
    recover_job = context.client.get('/api/jobs/' + recover_detail['job_id']).json()
    assert recover_job['needs_reconcile'] is True


@pytest.mark.parametrize('line, action', [
    (LOCKED_FATAL_LINE, 'close_and_retry'),
    (READ_ONLY_FATAL_LINE, 'clear_read_only_retry'),
])
def test_delete_file_fatal_does_not_reconcile(context, monkeypatch, line, action):
    event = json.loads(line)
    scripted(monkeypatch, [event], 1)
    context.api.remove_track = jsymphonic.remove_track
    etag = context.client.get('/api/tracks').headers['etag']
    deleted = context.client.delete('/api/tracks/1', headers={'If-Match': etag})
    assert deleted.status_code == 409, deleted.text
    detail = deleted.json()['detail']
    assert detail['fatal_code'] == event['code']
    assert detail['fatal_path'] == event['path']
    assert detail['recovery_action'] == action
    assert detail['code'] == 'delete_failed'
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['fatal_path'] == event['path']


@pytest.mark.parametrize('line, action', [
    (LOCKED_FATAL_LINE, 'close_and_retry'),
    (READ_ONLY_FATAL_LINE, 'clear_read_only_retry'),
])
def test_add_file_fatal_does_not_reconcile(context, monkeypatch, line, action):
    from test_api import imported
    event = json.loads(line)
    scripted(monkeypatch, [event], 1)
    context.api.add_tracks = jsymphonic.add_tracks
    job = imported(context)
    response = context.client.post('/api/transfers', json={'media_ids': [job['files'][0]['media_id']]})
    assert response.status_code == 200, response.text
    result = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert result['needs_reconcile'] is False
    row = result['files'][0]
    assert row['state'] == 'failed'
    assert row['fatal_code'] == event['code']
    assert row['fatal_path'] == event['path']
    assert row['recovery_action'] == action


def test_inspect_read_only_file_passes_the_path(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_file_read_only')
    response = context.client.get(INSPECT)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['fatal_code'] == 'DEVICE_FILE_READ_ONLY'
    assert detail['fatal_path'] == 'OMGAUDIO/10F00/10000001.OMA'
    assert detail['recovery_action'] == 'clear_read_only_retry'
    assert context.app.state.jobs.latest() is None


def test_locked_file_without_a_string_path_omits_fatal_path(context, monkeypatch):
    bind_script(context, monkeypatch, [{
        'event': 'fatal',
        'message': 'locked path OMGAUDIO/10F00/1000.mp3',
        'code': 'DEVICE_FILE_LOCKED',
        'path': {'file': 'OMGAUDIO/10F00/1000.mp3'},
    }], 1)
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['fatal_code'] == 'DEVICE_FILE_LOCKED'
    assert detail['recovery_action'] == 'close_and_retry'
    assert detail['code'] == 'playlist_failed'
    assert 'fatal_path' not in detail
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert 'fatal_path' not in job['files'][0]


def test_rollback_failure_needs_reconcile(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_rollback_failed')
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    event = json.loads(ROLLBACK_FATAL_LINE)
    assert detail['fatal_code'] == event['code']
    assert detail['recovery_action'] == 'inspect_recover'
    assert detail['recovery_action'] == jsymphonic.recovery_action_for('DEVICE_ROLLBACK_FAILED')
    assert detail['code'] == 'verify_device_state'
    assert 'path' not in event
    assert 'fatal_path' not in detail
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['files'][0]['detail'] == event['message']
    assert job['needs_reconcile'] is True
    assert job['files'][0]['state'] == 'unknown'
    assert job['files'][0]['fatal_code'] == 'DEVICE_ROLLBACK_FAILED'
    assert 'fatal_path' not in job['files'][0]


def test_playlist_create_rollback_needs_reconcile(context, monkeypatch):
    from test_playlists import native_context
    native_context(context)
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [sys.executable, str(FAKE_SHIM), 'playlist_rollback_failed'])
    context.api.create_playlist = jsymphonic.create_playlist
    etag = context.client.get('/api/device/playlists').headers['etag']
    response = context.client.post(
        '/api/device/playlists',
        headers={'If-Match': etag},
        json={'name': 'Walk', 'track_ids': ['1']},
    )
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    event = json.loads(ROLLBACK_FATAL_LINE)
    assert detail['fatal_code'] == event['code']
    assert detail['recovery_action'] == jsymphonic.recovery_action_for('DEVICE_ROLLBACK_FAILED')
    assert detail['recovery_action'] == 'inspect_recover'
    assert detail['code'] == 'verify_device_state'
    assert 'fatal_path' not in detail
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is True
    assert job['files'][0]['state'] == 'unknown'
    assert job['files'][0]['fatal_code'] == 'DEVICE_ROLLBACK_FAILED'
    assert job['files'][0]['detail'] == event['message']
    assert 'fatal_path' not in job['files'][0]


def test_inspect_locked_file_passes_the_path(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_file_locked')
    response = context.client.get(INSPECT)
    assert response.status_code == 409, response.text
    event = json.loads(LOCKED_FATAL_LINE)
    assert response.json()['detail'] == {
        'message': event['message'],
        'fatal_code': event['code'],
        'fatal_path': event['path'],
        'recovery_action': 'close_and_retry',
    }
    assert context.app.state.jobs.latest() is None


@pytest.mark.parametrize('call, reconcile', [
    (jsymphonic.inspect_playlist_journal, False),
    (jsymphonic.recover_playlist_journal, True),
    (jsymphonic.repair_playlists, True),
])
def test_exit_zero_without_done_is_not_success(monkeypatch, call, reconcile):
    scripted(monkeypatch, [{'event': 'playlistJournal', 'state': 'committed', 'outcome': 'rolled_forward',
                            'files': ['OMGAUDIO/10F00/1000.mp3']}])
    with pytest.raises(JSymphonicError, match='done') as error:
        call('fixture')
    assert error.value.needs_reconcile is reconcile
    assert jsymphonic.wait_for_idle(0)


@pytest.mark.parametrize('path', [RECOVER, REPAIR])
def test_mutation_without_done_needs_reconcile(context, monkeypatch, path):
    bind_script(context, monkeypatch, [{'event': 'playlistJournal', 'outcome': 'discarded', 'state': 'uncommitted'}])
    response = context.client.post(path)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'verify_device_state'
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is True
    assert job['files'][0]['state'] == 'unknown'


def test_inspect_without_done_creates_no_job(context, monkeypatch):
    bind_script(context, monkeypatch, [{'event': 'playlistJournal', 'state': 'committed', 'files': ['a.mp3']}])
    response = context.client.get(INSPECT)
    assert response.status_code == 409, response.text
    assert 'done' in response.json()['detail']
    assert context.app.state.jobs.latest() is None


@pytest.mark.parametrize('call, timeout_name, reconcile', [
    (jsymphonic.inspect_playlist_journal, 'INFO_TIMEOUT', False),
    (jsymphonic.recover_playlist_journal, 'DEL_TIMEOUT', True),
    (jsymphonic.repair_playlists, 'DEL_TIMEOUT', True),
])
def test_recovery_timeout_kills_the_shim(monkeypatch, call, timeout_name, reconcile):
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [sys.executable, str(FAKE_SHIM), 'hang'])
    monkeypatch.setattr(jsymphonic, timeout_name, 0.4)
    started = time.monotonic()
    with pytest.raises(JSymphonicError, match='timed out') as error:
        call('fixture')
    assert time.monotonic() - started < 8
    assert error.value.needs_reconcile is reconcile
    assert jsymphonic.wait_for_idle(0)


def test_inspect_holds_the_shim_lock_until_it_finishes(monkeypatch):
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [sys.executable, str(FAKE_SHIM), 'hang'])
    monkeypatch.setattr(jsymphonic, 'INFO_TIMEOUT', 0.6)
    held = threading.Event()

    def watch():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not jsymphonic.wait_for_idle(0):
                held.set()
                return
            time.sleep(0.02)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        with pytest.raises(JSymphonicError, match='timed out'):
            jsymphonic.inspect_playlist_journal('fixture')
    finally:
        watcher.join(5)
    assert held.is_set()
    assert jsymphonic.wait_for_idle(0)


@pytest.mark.parametrize('path', [RECOVER, REPAIR])
def test_concurrent_recovery_requests_do_not_interleave(context, monkeypatch, tmp_path, path):
    stamp_dir = tmp_path / 'stamps'
    stamp_dir.mkdir()
    script = tmp_path / 'recovery_stamp.py'
    script.write_text(
        'import json, os, sys, time\n'
        'from pathlib import Path\n'
        f'stamp_dir = Path({str(stamp_dir)!r})\n'
        'command = sys.argv[1]\n'
        'if command == "list":\n'
        '    print(json.dumps({"event": "track", "id": "1", "title": "Track", "artist": "A", "album": "B"}), flush=True)\n'
        '    print(json.dumps({"event": "listEnd", "count": 1}), flush=True)\n'
        '    raise SystemExit(0)\n'
        'if command in ("playlist-recover", "playlist-repair"):\n'
        '    start = time.time()\n'
        '    time.sleep(0.35)\n'
        '    end = time.time()\n'
        '    Path(stamp_dir, str(os.getpid())).write_text(json.dumps({"start": start, "end": end}))\n'
        '    if command == "playlist-repair":\n'
        '        print(json.dumps({"event": "playlistRepair", "prunedCount": 0, "prunedTrackIds": [], "playlistIds": []}), flush=True)\n'
        '    else:\n'
        '        print(json.dumps({"event": "playlistJournal", "state": "none", "outcome": "none"}), flush=True)\n'
        '    print(json.dumps({"event": "done"}), flush=True)\n'
        '    raise SystemExit(0)\n'
        'print(json.dumps({"event": "fatal", "message": "unexpected " + command}), flush=True)\n'
        'raise SystemExit(1)\n'
    )
    monkeypatch.setattr(jsymphonic, 'SHIM_CMD_PREFIX', [sys.executable, str(script)])
    context.api.list_tracks = jsymphonic.list_tracks
    context.api.recover_playlist_journal = jsymphonic.recover_playlist_journal
    context.api.repair_playlists = jsymphonic.repair_playlists
    responses = []
    barrier = threading.Barrier(2)

    def post():
        barrier.wait(5)
        responses.append(context.client.post(path))

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads)
    assert len(responses) == 2
    assert [response.status_code for response in responses] == [200, 200]
    stamps = [json.loads(item.read_text()) for item in stamp_dir.iterdir()]
    assert len(stamps) == 2
    (first, second) = sorted(stamps, key=lambda item: item['start'])
    assert first['end'] <= second['start']
    assert context.client.get('/api/engine-busy').json()['busy'] is False


def test_recovery_routes_reject_after_drain(context):
    context.client.post('/api/shutdown/drain')
    assert context.client.get(INSPECT).status_code == 409
    assert context.client.post(RECOVER).status_code == 409
    assert context.client.post(REPAIR).status_code == 409
    assert context.app.state.jobs.latest() is None


def test_rollback_needs_reconcile_even_on_a_read(monkeypatch):
    scripted(monkeypatch, [{'event': 'fatal', 'message': 'rollback failed', 'code': 'DEVICE_ROLLBACK_FAILED'}], 1)
    with pytest.raises(JSymphonicError) as error:
        jsymphonic.inspect_playlist_journal('fixture')
    assert error.value.needs_reconcile is True
    assert error.value.code == 'DEVICE_ROLLBACK_FAILED'


def _probe_warning():
    event = json.loads(PROBE_PENDING_WARNING_LINE)
    return {'code': event['code'], 'path': event['path'], 'probe_path': event['probe_path']}


@pytest.mark.parametrize('path, line, action', [
    (REPAIR, PROBE_RESTORE_FATAL_LINE, 'inspect_recover'),
    (RECOVER, PROBE_RESTORE_FATAL_LINE, 'inspect_recover'),
    (REPAIR, PROBE_CONFLICT_FATAL_LINE, 'manual_help'),
    (RECOVER, PROBE_CONFLICT_FATAL_LINE, 'manual_help'),
])
def test_probe_fatals_need_reconcile(context, monkeypatch, path, line, action):
    event = json.loads(line)
    scenario = 'playlist_probe_restore_failed' if event['code'] == 'DEVICE_PROBE_RESTORE_FAILED' else 'playlist_probe_conflict'
    use_fake(monkeypatch, context, scenario)
    response = context.client.post(path)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['fatal_code'] == event['code']
    assert detail['fatal_path'] == event['path']
    assert detail['fatal_probe_path'] == event['probe_path']
    assert detail['recovery_action'] == action
    assert detail['recovery_action'] == jsymphonic.recovery_action_for(event['code'])
    assert detail['code'] == 'verify_device_state'
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is True
    assert job['files'][0]['state'] == 'unknown'
    assert job['files'][0]['fatal_code'] == event['code']
    assert job['files'][0]['fatal_path'] == event['path']
    assert job['files'][0]['fatal_probe_path'] == event['probe_path']


def test_probe_path_markup_passes_through_unchanged(context, monkeypatch):
    bind_script(context, monkeypatch, [{
        'event': 'fatal',
        'message': 'Device file probe conflicts with the track',
        'code': 'DEVICE_PROBE_CONFLICT',
        'path': 'OMGAUDIO/10F00/10000001.OMA',
        'probe_path': MARKUP_PROBE_PATH,
    }], 1)
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['fatal_probe_path'] == MARKUP_PROBE_PATH
    assert '<b>' in detail['fatal_probe_path']
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['files'][0]['fatal_probe_path'] == MARKUP_PROBE_PATH


def test_probe_pending_warning_on_track_list(context, monkeypatch):
    warning = json.loads(PROBE_PENDING_WARNING_LINE)
    scripted(monkeypatch, [
        warning,
        {'event': 'track', 'id': '1', 'title': 'Alpha', 'artist': 'A', 'album': 'AA', 'durationSeconds': 1},
        {'event': 'listEnd', 'count': 1},
    ])
    context.api.list_tracks = jsymphonic.list_tracks
    response = context.client.get('/api/tracks')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['items'][0]['id'] == '1'
    assert body['warnings'] == [_probe_warning()]


def test_probe_pending_warning_on_device_details(monkeypatch):
    warning = json.loads(PROBE_PENDING_WARNING_LINE)
    scripted(monkeypatch, [warning, {'event': 'device', 'ok': True, 'generation': 1}])
    body = jsymphonic.device_details('fixture')
    assert body['ok'] is True
    assert body['warnings'] == [_probe_warning()]


def test_probe_pending_warning_on_playlists(context, monkeypatch):
    warning = json.loads(PROBE_PENDING_WARNING_LINE)
    scripted(monkeypatch, [warning, {'event': 'playlistsEnd', 'count': 0}])
    context.api.list_playlists = jsymphonic.list_playlists
    response = context.client.get('/api/device/playlists')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['items'] == []
    assert body['warnings'] == [_probe_warning()]


def test_probe_pending_warning_on_inspect(context, monkeypatch):
    warning = json.loads(PROBE_PENDING_WARNING_LINE)
    bind_script(context, monkeypatch, [
        warning,
        {'event': 'playlistJournal', 'state': 'none', 'files': []},
        {'event': 'done'},
    ])
    response = context.client.get(INSPECT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['state'] == 'none'
    assert body['warnings'] == [_probe_warning()]
    assert context.app.state.jobs.latest() is None


def test_player_has_no_playlist_recovery_routes(tmp_path):
    app = create_app(product='player', data_dir=tmp_path, token=TOKEN, origin=ORIGIN, scanner=Scanner())
    assert not any('playlist-recovery' in getattr(route, 'path', '') for route in app.routes)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        assert client.get(INSPECT).status_code == 404
        # POST is 405 when the packaged frontend mount is present, same as other device writes.
        assert client.post(RECOVER).status_code in (404, 405)
        assert client.post(REPAIR).status_code in (404, 405)
