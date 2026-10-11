"""Playlist recovery endpoints driven by the fake shim (SHIM_CMD_PREFIX).

Present `state` / `outcome` values are passed through. Missing and unknown
values stay null. Message text is never a substitute.
"""
import json
import sys
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


def test_inspect_committed_journal_passes_coverage_ids(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_inspect_committed')
    body = context.client.get(INSPECT).json()
    assert body['covers'] == {'playlist_ids': ['4'], 'track_ids': ['11', '1']}


def test_inspect_without_id_fields_has_null_covers(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_inspect_missing')
    assert context.client.get(INSPECT).json()['covers'] is None


def test_step_state_does_not_hide_a_journal_state(monkeypatch):
    scripted(monkeypatch, [
        {'event': 'step', 'state': 'finished', 'message': 'uncommitted'},
        {'event': 'playlistJournal', 'state': 'committed'},
    ])
    assert jsymphonic.inspect_playlist_journal('fixture')['state'] == 'committed'


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


def test_repair_pruned_ids_pass_through(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_some')
    body = context.client.post(REPAIR).json()
    assert body['pruned_count'] == 2
    assert body['pruned_track_ids'] == ['7', '9']
    assert body['playlist_ids'] == ['4']


def test_repair_missing_count_stays_unknown(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_missing')
    body = context.client.post(REPAIR).json()
    assert body['ok'] is True
    assert body['pruned_count'] is None
    assert body['pruned_track_ids'] is None
    assert body['playlist_ids'] is None


def test_repair_empty_mount_is_not_silent_success(context, monkeypatch):
    use_fake(monkeypatch, context, 'playlist_repair_empty_mount')
    response = context.client.post(REPAIR)
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['empty_mount'] is True
    assert detail['code'] == 'playlist_failed'
    assert detail['fatal_code'] is None
    assert 'empty' in detail['message']
    job = context.client.get('/api/jobs/' + detail['job_id']).json()
    assert job['needs_reconcile'] is False
    assert job['files'][0]['state'] == 'failed'
    assert job['files'][0]['empty_mount'] is True


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
    assert error.value.empty_mount is False


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


def test_player_has_no_playlist_recovery_routes(tmp_path):
    app = create_app(product='player', data_dir=tmp_path, token=TOKEN, origin=ORIGIN, scanner=Scanner())
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        assert client.get(INSPECT).status_code == 404
        assert client.post(RECOVER).status_code == 404
        assert client.post(REPAIR).status_code == 404
