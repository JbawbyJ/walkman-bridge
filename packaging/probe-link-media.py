"""Operator-only real public download -> Defender -> ffmpeg -> range proof.

Uses isolated player state and no device modules. Run explicitly; not a CI network test.
"""
import json
import secrets
import sys
import tempfile
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from fastapi.testclient import TestClient
from application import create_app
from transcode import _ensure_ffmpeg


def main():
    output = ROOT / 'packaging/build/media-upgrade'
    output.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix='live-api-', dir=output))
    origin, token = 'http://127.0.0.1:48127', secrets.token_urlsafe(48)
    app = create_app(product='player', data_dir=state, token=token, origin=origin)
    report = {'passed': False, 'actual_defender': True, 'providers': []}
    try:
        with TestClient(app, base_url=origin, headers={'X-NightOps-Token': token}) as client:
            for provider, url in (
                ('youtube', 'https://www.youtube.com/watch?v=jNQXAC9IVRw'),
                ('soundcloud', 'https://soundcloud.com/giovannisarani/mezzo-valzer'),
            ):
                admitted = client.post('/api/media/import-link', json={'url': url})
                admitted.raise_for_status()
                job = client.get('/api/jobs/' + admitted.json()['job_id']).json()
                assert job['status'] == 'done', job
                media_id = job['files'][0]['media_id']
                record = app.state.store.get(media_id)
                ranged = client.get(f'/api/media/{media_id}/stream', headers={'Range': 'bytes=0-15'})
                assert ranged.status_code == 206 and len(ranged.content) == 16
                prepared = client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'})
                prepared.raise_for_status()
                result = client.get('/api/jobs/' + prepared.json()['job_id']).json()
                assert result['status'] == 'done', result
                derivative = client.get(f'/api/media/{media_id}/stream', headers={'Range': 'bytes=0-3'})
                assert derivative.status_code == 206 and derivative.content == b'fLaC'
                final = app.state.store.get(media_id)
                entry = dict(provider=provider, title=record['title'], artist=record['artist'],
                    source_bytes=record['artifacts']['source']['size_bytes'],
                    source_cleared=record['artifacts']['source']['scan']['ok'],
                    flac_cleared=final['artifacts']['playback']['scan']['ok'],
                    duration_seconds=record.get('duration_seconds'), authenticated_ranges=True)
                report['providers'].append(entry)
                print(json.dumps(entry), flush=True)
            cover, source = state / 'fixture-cover.png', state / 'fixture-with-cover.mp3'
            def generate(*arguments):
                subprocess.run([_ensure_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', *map(str, arguments)],
                    check=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            generate('-f', 'lavfi', '-i', 'color=c=red:size=1024x768', '-frames:v', '1', cover)
            generate('-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', '-i', cover,
                '-map', '0:a', '-map', '1:v', '-codec:a', 'libmp3lame', '-codec:v', 'copy',
                '-id3v2_version', '3', '-disposition:v:0', 'attached_pic', source)
            imported = client.post('/api/media/import', files={'files': ('Cover proof.mp3', source.read_bytes(), 'audio/mpeg')})
            imported.raise_for_status()
            job = client.get('/api/jobs/' + imported.json()['job_id']).json()
            assert job['status'] == 'done', job
            media_id = job['files'][0]['media_id']
            record = app.state.store.get(media_id)
            assert record['artifacts']['source']['scan']['ok']
            assert record['artwork_status'] == 'ready' and record['artifacts']['artwork']['scan']['ok']
            picture = client.get(f'/api/media/{media_id}/artwork')
            assert picture.status_code == 200 and picture.content.startswith(b'\xff\xd8\xff')
            report['artwork'] = dict(source_cleared=True, jpeg_cleared=True, authenticated=True, jpeg_bytes=len(picture.content))
            report['device_modules_loaded'] = any(name in sys.modules for name in ('device', 'jsymphonic'))
            assert not report['device_modules_loaded']
            assert not client.get('/api/engine-busy').json()['busy']
            report['passed'] = True
    finally:
        (output / 'live-link-api-proof.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
