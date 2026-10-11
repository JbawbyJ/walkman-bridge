"""Playlist recovery endpoints driven by the fake shim (SHIM_CMD_PREFIX).

Present `state` / `outcome` values are passed through from `playlistJournal`.
Missing and unknown values stay null. Message text is never a substitute.
Repair summary ids are JSON numbers and come back as strings. A final `done`
event is required.
"""
import json
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
    assert body['covers'] == {'files': ['OMGAUDIO/10F00/1000.mp3', 'OMGAUDIO/10F00/1001.mp3']}
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
        {'event': 'playlistJournal', 'state': 'committed', 'files': ['OMGAUDIO/10F00/1000.mp3']},
        {'event': 'done', 'state': 'uncommitted', 'outcome': 'discarded', 'files': ['ignored.mp3']},
    ])
    body = jsymphonic.inspect_playlist_journal('fixture')
    assert body['state'] == 'committed'
    assert body['covers'] == {'files': ['OMGAUDIO/10F00/1000.mp3']}


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
    assert 'empty_mount' not in detail
    assert context.calls['list'] == 1
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'


def test_repair_reuses_a_cached_empty_track_list(context, monkeypatch):
    context.tracks.clear()
    assert context.client.get('/api/tracks').json() == []
    assert context.calls['list'] == 1

    def explode(mount):
        raise AssertionError('cached track list should be reused')

    context.api.list_tracks = explode
    use_fake(monkeypatch, context, 'playlist_repair_some')
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['fatal_code'] == 'PLAYLIST_LIBRARY_NOT_LOADED'
    assert response.json()['detail']['code'] == 'library_not_loaded'


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
    assert context.calls['list'] == 2


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
        assert response.json()['detail'] == {'message': 'blocked PLAYLIST_JOURNAL_PENDING', 'fatal_code': code}
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


def test_locked_file_passes_path_and_does_not_reconcile(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_file_locked')
    response = context.client.post(RECOVER)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['fatal_code'] == 'DEVICE_FILE_LOCKED'
    assert detail['fatal_path'] == 'OMGAUDIO/10F00/1000.mp3'
    assert detail['code'] == 'playlist_failed'
    assert detail['fatal_path'] != 'nested.mp3'
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['fatal_code'] == 'DEVICE_FILE_LOCKED'
    assert job['files'][0]['fatal_path'] == 'OMGAUDIO/10F00/1000.mp3'


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
    assert 'fatal_path' not in detail
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert 'fatal_path' not in job['files'][0]


def test_rollback_failure_needs_reconcile(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_rollback_failed')
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['fatal_code'] == 'DEVICE_ROLLBACK_FAILED'
    assert detail['code'] == 'verify_device_state'
    assert 'fatal_path' not in detail
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is True
    assert job['files'][0]['state'] == 'unknown'
    assert job['files'][0]['fatal_code'] == 'DEVICE_ROLLBACK_FAILED'
    assert 'fatal_path' not in job['files'][0]


def test_inspect_locked_file_passes_the_path(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_file_locked')
    response = context.client.get(INSPECT)
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == {
        'message': 'device file is locked path elsewhere',
        'fatal_code': 'DEVICE_FILE_LOCKED',
        'fatal_path': 'OMGAUDIO/10F00/1000.mp3',
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


def test_player_has_no_playlist_recovery_routes(tmp_path):
    app = create_app(product='player', data_dir=tmp_path, token=TOKEN, origin=ORIGIN, scanner=Scanner())
    assert not any('playlist-recovery' in getattr(route, 'path', '') for route in app.routes)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        assert client.get(INSPECT).status_code == 404
        # POST is 405 when the packaged frontend mount is present, same as other device writes.
        assert client.post(RECOVER).status_code in (404, 405)
        assert client.post(REPAIR).status_code in (404, 405)
