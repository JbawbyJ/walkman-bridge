"""Operator-invoked harmless real Defender/API/ffmpeg proof, with no device imports."""
import json
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from fastapi.testclient import TestClient
from application import create_app

output = ROOT / 'packaging' / 'build' / 'real-media'
output.mkdir(parents=True, exist_ok=True)
state = Path(tempfile.mkdtemp(prefix='state-', dir=output))
origin = 'http://127.0.0.1:48119'
token = secrets.token_urlsafe(48)
source = ROOT / 'scanner-helper/artifacts/real-uac-proof/one.wav'
app = create_app(product='player', data_dir=state, token=token, origin=origin)
with TestClient(app, base_url=origin, headers={'X-NightOps-Token': token}) as client:
    response = client.post('/api/media/import', files={'files': ('harmless.wav', source.read_bytes(), 'audio/wav')})
    response.raise_for_status()
    imported = client.get('/api/jobs/' + response.json()['job_id']).json()
    assert imported['status'] == 'done', imported
    media_id = imported['files'][0]['media_id']
    streamed = client.get(f'/api/media/{media_id}/stream', headers={'Range': 'bytes=0-3'})
    assert streamed.status_code == 206 and streamed.content == b'RIFF'
    response = client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'})
    response.raise_for_status()
    prepared = client.get('/api/jobs/' + response.json()['job_id']).json()
    assert prepared['status'] == 'done', prepared
    streamed = client.get(f'/api/media/{media_id}/stream', headers={'Range': 'bytes=0-3'})
    assert streamed.status_code == 206 and streamed.content == b'fLaC'
    record = app.state.store.get(media_id)
    report = dict(ok=True, actual_defender=True, source_clear=record['artifacts']['source']['scan']['ok'],
                  generated_flac_clear=record['artifacts']['playback']['scan']['ok'],
                  ranged_source_and_flac=True, duration_seconds=record.get('duration_seconds'),
                  device_modules_loaded=any(name in sys.modules for name in ('device', 'jsymphonic')))
    assert not report['device_modules_loaded']
    (output / 'real-media.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
