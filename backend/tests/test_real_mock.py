"""Fresh real ffmpeg + Java + API round trip. No physical drive discovery.

Opt in with NIGHTOPS_REAL_MOCK=1 and resolved ffmpeg/Java paths. Defender is
injected at the scanner boundary here; real Defender is tested separately with
harmless audio. This test flag is never read by production modules.
"""
import io
import math
import os
import struct
import wave
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from main import create_app
from test_api import Scanner, TOKEN, ORIGIN
import device
import jsymphonic

@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='opt-in real ffmpeg/Java mock integration')
def test_api_ffmpeg_java_backup_delete(tmp_path):
    mount = tmp_path / 'mock-walkman'
    (mount / 'OMGAUDIO').mkdir(parents=True)
    api = SimpleNamespace(find_walkman=lambda: mount, capture_device_identity=device.capture_device_identity,
        device_info=device.device_info, backup_device=device.backup_device,
        list_tracks=jsymphonic.list_tracks, add_tracks=jsymphonic.add_tracks,
        remove_track=jsymphonic.remove_track, JSymphonicError=jsymphonic.JSymphonicError)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<hh', sample, sample) for sample in
            (int(2000 * math.sin(2 * math.pi * 440 * index / 44100)) for index in range(44100))))
    app = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN, origin=ORIGIN, scanner=Scanner(), device_api=api)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.post('/api/media/import', files={'files': ('roundtrip.wav', buffer.getvalue(), 'audio/wav')})
        assert response.status_code == 200, response.text
        job = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert job['status'] == 'done', job
        media_id = job['files'][0]['media_id']
        assert client.get(f'/api/media/{media_id}/stream', headers={'Range': 'bytes=0-3'}).content == b'RIFF'
        result = client.post('/api/transfers', json={'media_ids': [media_id]})
        job = client.get('/api/jobs/' + result.json()['job_id']).json()
        assert job['status'] == 'done', job
        assert job['files'][0]['state'] == 'transferred'
        tracks = client.get('/api/tracks?refresh=1')
        assert len(tracks.json()) == 1
        assert len(list((mount / 'OMGAUDIO').glob('*.DAT'))) == 17
        assert len(list((mount / 'OMGAUDIO').rglob('*.OMA'))) == 1
        backup = client.post('/api/backup')
        assert backup.status_code == 200, backup.text
        from pathlib import Path
        destination = Path(backup.json()['path'])
        for file in mount.rglob('*'):
            if file.is_file():
                assert (destination / file.relative_to(mount)).read_bytes() == file.read_bytes()
        deleted = client.delete('/api/tracks/' + str(tracks.json()[0]['id']), headers={'If-Match': tracks.headers['etag']})
        assert deleted.status_code == 200, deleted.text
        assert client.get('/api/tracks?refresh=1').json() == []
        assert client.get('/api/queue').json()['items'][0]['id'] == media_id
