"""Explicit operator test on the already backed-up Sony. Never run in CI.

Uses production auth, Defender, coordinator, ffmpeg and Java. Only a generated
test track is deleted; a two-track Night Ops Check playlist remains for firmware
acceptance. Stop on uncertainty; never replay writes or restore automatically.
"""
import argparse
import hashlib
import io
import json
import math
import secrets
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from fastapi.testclient import TestClient
from application import create_app
import device
import jsymphonic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        parser.error('Explicit --apply is required for the operator-authorized device test')
    proof = json.loads((ROOT / 'hardware-proof/playlist-prewrite-backup.json').read_text(encoding='utf-8'))
    mount, backup = Path(proof['mount']), Path(proof['backup'])
    identity = device.capture_device_identity(mount)
    assert identity.volume_id == proof['identity']['volume_id'], 'Original Sony volume required'
    assert device.find_walkman() == mount, 'Detected device must match verified backup identity'
    before = device._tree_manifest(mount)
    expected = {key: tuple(value) for key, value in proof['files'].items()}
    assert device._tree_manifest(backup) == expected == before, 'Device or immutable backup changed; stop'
    out = ROOT / 'hardware-proof/management-0.4'
    out.mkdir(exist_ok=True)
    report = {'passed': False, 'backup': str(backup), 'volume_id': identity.volume_id,
        'jar_sha256': hashlib.sha256(jsymphonic.JAR_PATH.read_bytes()).hexdigest(), 'steps': []}
    def record(step, **values):
        report['steps'].append({'step': step, **values})
        (out / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(step, flush=True)
    state = Path(tempfile.mkdtemp(prefix='state-', dir=out))
    origin, token = 'http://127.0.0.1:48228', secrets.token_urlsafe(48)
    app = create_app(product='bridge', data_dir=state, token=token, origin=origin)
    try:
        with TestClient(app, base_url=origin, headers={'X-NightOps-Token': token}) as client:
            def call(method, path, **kwargs):
                device.revalidate_device(identity)
                response = client.request(method, path, **kwargs)
                assert response.is_success, (path, response.status_code, response.text)
                return response
            def finished(response):
                job = call('GET', '/api/jobs/' + response.json()['job_id']).json()
                assert job['status'] == 'done' and not job['needs_reconcile'], job
                return job
            original_tracks = call('GET', '/api/tracks?refresh=1').json()
            assert len(original_tracks) == 14
            original_ids = {str(t['id']) for t in original_tracks}
            original_lists = call('GET', '/api/device/playlists').json()['items']
            assert not any(p['name'] == 'Night Ops Check' for p in original_lists)
            record('Verified full backup, volume identity and original 14-track ledger')
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as audio:
                audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                audio.writeframes(b''.join(struct.pack('<hh', sample, sample) for sample in
                    (int(1200 * math.sin(2 * math.pi * 440 * i / 44100)) for i in range(88200))))
            imported = finished(call('POST', '/api/media/import', files={'files': ('Night Ops harmless test.wav', buffer.getvalue(), 'audio/wav')}))
            media_id = imported['files'][0]['media_id']
            call('PATCH', f'/api/media/{media_id}/metadata', json={'title': 'Night Ops hardware check', 'artist': 'Red Lotus', 'album': 'Deployment verification', 'genre': 'Test', 'year': '2026', 'track': '1'})
            source = app.state.store.get(media_id)['artifacts']['source']['scan']
            assert source['ok'] and source['defender_signature_version']
            record('Real Defender cleared generated source', scan=source)
            transferred = finished(call('POST', '/api/transfers', json={'media_ids': [media_id]}))
            assert transferred['files'][0]['state'] == 'transferred'
            tracks = call('GET', '/api/tracks?refresh=1').json()
            extra = [t for t in tracks if str(t['id']) not in original_ids]
            assert len(extra) == 1 and len(tracks) == 15, tracks
            test_id = str(extra[0]['id'])
            assert extra[0]['title'] == 'Night Ops hardware check' and extra[0]['artist'] == 'Red Lotus'
            derivative = app.state.store.get(media_id)['artifacts']['transfer']['scan']
            assert derivative['ok'] and derivative['defender_signature_version']
            record('Transferred one generated 192 kbps MP3 and read its music details from Sony', track=extra[0], scan=derivative)
            snapshot = call('GET', '/api/device/playlists')
            assert snapshot.json()['items'] == original_lists
            made = call('POST', '/api/device/playlists', headers={'If-Match': snapshot.headers['etag']},
                json={'name': 'Night Ops \u65e5\u672c', 'track_ids': [str(original_tracks[0]['id']), test_id]})
            finished(made)
            playlist_id = made.json()['playlist']['id']
            snapshot = call('GET', '/api/device/playlists')
            assert any(p['name'] == 'Night Ops \u65e5\u672c' for p in snapshot.json()['items'])
            changed = call('PATCH', '/api/device/playlists/' + playlist_id, headers={'If-Match': snapshot.headers['etag']},
                json={'name': 'Night Ops Check', 'track_ids': [test_id, str(original_tracks[1]['id'])]})
            finished(changed)
            record('Created, read, renamed and reordered native Unicode playlist')
            snapshot = call('GET', '/api/device/playlists')
            finished(call('DELETE', '/api/device/playlists/' + playlist_id, headers={'If-Match': snapshot.headers['etag']}))
            tracks = call('GET', '/api/tracks?refresh=1')
            assert test_id not in original_ids
            finished(call('DELETE', '/api/tracks/' + test_id, headers={'If-Match': tracks.headers['etag']}))
            assert call('GET', '/api/tracks?refresh=1').json() == original_tracks
            assert call('GET', '/api/device/playlists').json()['items'] == original_lists
            record('Deleted only generated test track and test playlist; original ledger matches')
            snapshot = call('GET', '/api/device/playlists')
            demo = call('POST', '/api/device/playlists', headers={'If-Match': snapshot.headers['etag']},
                json={'name': 'Night Ops Check', 'track_ids': [str(t['id']) for t in original_tracks[:2]]})
            finished(demo)
            report['firmware_check_playlist'] = demo.json()['playlist']
            report['firmware_check_tracks'] = original_tracks[:2]
            after = device._tree_manifest(mount)
            rebuilt = {f'OMGAUDIO/{prefix}{suffix}.DAT' for prefix in ('01TREE', '03GINF') for suffix in ('01', '02', '03', '04', '22', '2D')}
            rebuilt |= {'OMGAUDIO/02TREINF.DAT', 'OMGAUDIO/04CNTINF.DAT', 'OMGAUDIO/04PATLST.DAT', 'OMGAUDIO/05CIDLST.DAT'}
            changed_paths = [p for p in before if before[p] != after.get(p)]
            assert all(p.upper() in rebuilt for p in changed_paths), changed_paths
            assert all(after[p] == value for p, value in before.items() if p.upper().endswith('.OMA'))
            assert device._tree_manifest(backup) == before, 'Original backup must remain byte identical'
            assert not call('GET', '/api/engine-busy').json()['busy']
            call('POST', '/api/shutdown/drain')
            report.update(passed=True, originals_preserved=True, original_tracks=14,
                changed_paths=changed_paths, added_paths=[p for p in after if p not in before], firmware_verified=False)
            record('All 14 original OMA files unchanged; Night Ops Check remains for human firmware/menu verification')
    except Exception as exc:
        record('STOP: no automatic retry or restoration', error=str(exc))
        raise


if __name__ == '__main__':
    main()
