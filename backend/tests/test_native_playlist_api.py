"""Opt-in real API/Java native playlist round trip on a COPY of a Sony backup."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from application import create_app
import device
import jsymphonic


def test_real_native_playlist_api_preserves_original_device_files(tmp_path):
    configured = os.environ.get('NIGHTOPS_SONY_FIXTURE')
    if not configured:
        pytest.skip('Requires opt-in path to a verified local Sony BACKUP, never a device mount')
    fixture = Path(configured).resolve()
    # Explicitly refuse a volume root or anything outside this checkout fixture area.
    assert fixture.is_relative_to(Path(__file__).resolve().parents[2] / 'hardware-proof')
    mount = tmp_path / 'mock-sony'
    shutil.copytree(fixture, mount)
    before = device._tree_manifest(mount)
    api = SimpleNamespace(find_walkman=lambda: mount,
        **{name: getattr(device, name) for name in ('device_info', 'capture_device_identity', 'backup_device')},
        **{name: getattr(jsymphonic, name) for name in ('list_tracks', 'add_tracks', 'remove_track', 'list_playlists',
            'create_playlist', 'update_playlist', 'delete_playlist', 'JSymphonicError')})
    origin, token = 'http://127.0.0.1:48225', 'native-fixture-' + 'x' * 48
    app = create_app(product='bridge', device_api=api, data_dir=tmp_path / 'state', origin=origin, token=token)
    with TestClient(app, base_url=origin, headers={'X-NightOps-Token': token}) as client:
        original_tracks = client.get('/api/tracks').json()
        assert len(original_tracks) >= 2
        ids = [track['id'] for track in original_tracks[:2]]
        initial = client.get('/api/device/playlists')
        assert initial.status_code == 200, initial.text
        original_lists = initial.json()['items']
        made = client.post('/api/device/playlists', headers={'If-Match': initial.headers['etag']},
            json={'name': 'Night \u65e5\u672c \U0001f319', 'track_ids': [ids[1], ids[0], ids[1]]})
        assert made.status_code == 201, made.text
        playlist = made.json()['playlist']
        assert playlist['name'] == 'Night \u65e5\u672c \U0001f319'
        assert playlist['track_ids'] == [ids[1], ids[0], ids[1]]
        current = client.get('/api/device/playlists')
        assert playlist in current.json()['items']
        assert client.patch('/api/device/playlists/' + playlist['id'], headers={'If-Match': initial.headers['etag']},
            json={'name': 'stale'}).status_code == 409
        changed = client.patch('/api/device/playlists/' + playlist['id'], headers={'If-Match': current.headers['etag']},
            json={'name': '--device \u65e5\u672c', 'track_ids': ids})
        assert changed.status_code == 200, changed.text
        assert changed.json()['playlist']['name'] == '--device \u65e5\u672c'
        final_list = client.get('/api/device/playlists')
        assert client.delete('/api/device/playlists/' + playlist['id'], headers={'If-Match': final_list.headers['etag']}).status_code == 200
        assert client.get('/api/device/playlists').json()['items'] == original_lists
        assert client.get('/api/tracks').json() == original_tracks
        assert not client.get('/api/engine-busy').json()['busy']
    after = device._tree_manifest(mount)
    changed_paths = [name for name in set(before) | set(after) if before.get(name) != after.get(name)]
    assert all(name.upper() in ('OMGAUDIO/01TREE22.DAT', 'OMGAUDIO/03GINF22.DAT') for name in changed_paths), changed_paths
    metadata_before = (fixture / 'OMGAUDIO/03GINF22.DAT').read_bytes()
    metadata_after = (mount / 'OMGAUDIO/03GINF22.DAT').read_bytes()
    # Existing raw metadata slots, including unused Sony presets, are retained exactly.
    assert metadata_after[0x30:len(metadata_before)] == metadata_before[0x30:]
    evidence = dict(passed=True, original_tracks=len(original_tracks), exact_unicode=True,
        duplicate_members_preserved=True, stale_edit_rejected=True, original_lists_preserved=True,
        only_native_playlist_tables_changed=True, changed_paths=changed_paths,
        jar_sha256=hashlib.sha256(jsymphonic.JAR_PATH.read_bytes()).hexdigest())
    out = Path(__file__).resolve().parents[2] / 'packaging/build/management-upgrade'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'native-api-mock-proof.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
