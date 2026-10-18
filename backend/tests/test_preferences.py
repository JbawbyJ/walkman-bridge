"""Appearance persists independently of renderer origins and library state."""
import io
from types import SimpleNamespace
from fastapi.testclient import TestClient
from application import create_app
from media_store import MediaStore


def test_theme_survives_restart_and_origin_change_without_changing_music(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    store = MediaStore(state / 'nightops.sqlite', state / 'cache')
    track = store.import_file('keep.wav', io.BytesIO(b'original music'))
    store.playback_state({'media_id': track['id'], 'volume': 0.73, 'position_seconds': 11})
    original = store.playback_state()
    for port, expected, choice in [(8765, 'light', 'dark'), (9876, 'dark', 'light'), (8764, 'light', 'dark')]:
        origin = f'http://127.0.0.1:{port}'
        app = create_app(product='bridge', data_dir=state, token='appearance-test', origin=origin,
                         scanner=SimpleNamespace(), device_api=SimpleNamespace())
        with TestClient(app, base_url=origin, headers={'X-NightOps-Token': 'appearance-test'}) as client:
            assert client.get('/api/preferences').json() == {'theme': expected}
            assert client.patch('/api/preferences', json={'theme': choice}).json() == {'theme': choice}
            assert client.patch('/api/preferences', json={'theme': 'system'}).status_code == 422
            assert client.patch('/api/preferences', json={'theme': None}).status_code == 422
            assert client.get('/api/preferences').json() == {'theme': choice}
            assert client.get('/api/playback-state').json() == original
            assert client.get('/api/queue').json()['items'][0]['id'] == track['id']
    assert store.path(track).read_bytes() == b'original music'


def test_preferences_reject_unauthorized_writes(tmp_path):
    origin = 'http://127.0.0.1:8765'
    app = create_app(product='player', data_dir=tmp_path, token='appearance-test', origin=origin,
                     scanner=SimpleNamespace())
    with TestClient(app, base_url=origin) as client:
        assert client.patch('/api/preferences', json={'theme': 'dark'}).status_code == 403
