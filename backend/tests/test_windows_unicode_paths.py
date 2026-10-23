"""Real java.exe with paths outside the Windows ANSI code page.

Windows only, with the built engine jar and WALKMAN_BRIDGE_JAVA. The API
transfer also needs NIGHTOPS_REAL_MOCK=1 for ffmpeg. A shim run either
works or fails before launch, and never leaves a track needing reconciliation.
"""
import io
import math
import os
import shutil
import struct
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import device
import jsymphonic
from main import create_app
from test_api import ORIGIN, TOKEN, Scanner

NON_ANSI = 'Профиль 用户'  # outside cp1252, the hosted runners' code page
JAVA = os.environ.get('WALKMAN_BRIDGE_JAVA')

pytestmark = [
    pytest.mark.skipif(os.name != 'nt', reason='Windows java.exe argv conversion'),
    pytest.mark.skipif(not JAVA or not jsymphonic.JAR_PATH.is_file(),
                       reason='needs WALKMAN_BRIDGE_JAVA and backend/vendor/jsymphonic.jar'),
]


@pytest.fixture
def non_ansi(tmp_path):
    if jsymphonic._ansi_round_trips(NON_ANSI):
        pytest.skip('this code page represents the sample folder name')
    root = tmp_path / NON_ANSI
    root.mkdir()
    return root


def _mount(root):
    mount = root / 'mock walkman'
    (mount / 'OMGAUDIO').mkdir(parents=True)
    return mount


def _launchable(path):
    """java.exe gets `path` intact, as is or through its 8.3 form."""
    short = jsymphonic._windows_short_path(path)
    return jsymphonic._ansi_round_trips(path) or (short is not None and jsymphonic._ansi_round_trips(short))


def _assert_definite_failure(error):
    assert error.needs_reconcile is False
    assert jsymphonic.job_needs_reconcile(error, writing=True) is False
    assert 'Nothing was written to the Walkman' in str(error)


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='opt-in real ffmpeg/Java mock integration')
def test_transfer_from_a_profile_outside_the_code_page(non_ansi):
    mount = _mount(non_ansi)
    api = SimpleNamespace(find_walkman=lambda: mount, capture_device_identity=device.capture_device_identity,
        device_info=device.device_info, backup_device=device.backup_device,
        list_tracks=jsymphonic.list_tracks, add_tracks=jsymphonic.add_tracks,
        remove_track=jsymphonic.remove_track, JSymphonicError=jsymphonic.JSymphonicError)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<hh', sample, sample) for sample in
            (int(2000 * math.sin(2 * math.pi * 440 * index / 44100)) for index in range(44100))))
    # State and the managed cache live under the profile, as in %LOCALAPPDATA%.
    app = create_app(product='bridge', data_dir=non_ansi / 'state', token=TOKEN, origin=ORIGIN,
                     scanner=Scanner(), device_api=api)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.post('/api/media/import', files={'files': ('песня.wav', buffer.getvalue(), 'audio/wav')})
        assert response.status_code == 200, response.text
        media_id = client.get('/api/jobs/' + response.json()['job_id']).json()['files'][0]['media_id']
        result = client.post('/api/transfers', json={'media_ids': [media_id]})
        assert result.status_code == 200, result.text
        job = client.get('/api/jobs/' + result.json()['job_id']).json()
        assert job['status'] == 'done', job
        assert job['needs_reconcile'] is False
        assert job['files'][0]['state'] == 'transferred'
        assert not app.state.store.get(media_id).get('needs_reconcile')
        assert len(client.get('/api/tracks?refresh=1').json()) == 1


def test_engine_jar_in_a_folder_outside_the_code_page(non_ansi, monkeypatch):
    jar = non_ansi / 'vendor' / 'jsymphonic.jar'
    jar.parent.mkdir()
    shutil.copyfile(jsymphonic.JAR_PATH, jar)
    monkeypatch.setattr(jsymphonic, 'JAR_PATH', jar)
    mount = _mount(non_ansi)
    if _launchable(str(jar)):
        assert jsymphonic.device_details(mount)['ok'] is True
        assert list(jsymphonic.list_tracks(mount)) == []
    else:
        with pytest.raises(jsymphonic.JSymphonicError) as error:
            jsymphonic.add_tracks(mount, [non_ansi / 'song.mp3'], lambda _: None)
        _assert_definite_failure(error.value)


def test_java_runtime_in_a_folder_outside_the_code_page(non_ansi, monkeypatch):
    import _winapi
    runtime = non_ansi / 'jre'
    _winapi.CreateJunction(str(os.path.realpath(os.path.join(os.path.dirname(JAVA), '..'))), str(runtime))
    try:
        java = str(runtime / 'bin' / 'java.exe')
        monkeypatch.setenv('WALKMAN_BRIDGE_JAVA', java)
        mount = _mount(non_ansi)
        if _launchable(java):
            assert jsymphonic.device_details(mount)['ok'] is True
        else:
            with pytest.raises(jsymphonic.JSymphonicError) as error:
                jsymphonic.add_tracks(mount, [non_ansi / 'song.mp3'], lambda _: None)
            _assert_definite_failure(error.value)
    finally:
        os.rmdir(runtime)  # Removes the junction only, never the JDK behind it.
