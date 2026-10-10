"""Independent real ffmpeg -> authenticated API -> Java mock-device evidence.

Only NIGHTOPS_REAL_MOCK=1 opts these tests in. The scanner is injected at its
test boundary; this suite does not claim real Defender or firmware verification.
No device discovery occurs: every Java operation targets a newly created folder.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import device
import jsymphonic
import media_service
from main import create_app
from test_api import ORIGIN, TOKEN, Scanner


pytestmark = pytest.mark.skipif(
    os.environ.get('NIGHTOPS_REAL_MOCK') != '1',
    reason='opt-in real ffmpeg/Java metadata integration',
)


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout


def _generate(ffmpeg, destination, tags=None):
    options = []
    for key, value in (tags or {}).items():
        options.extend(['-metadata', f'{key}={value}'])
    _run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
          '-i', 'sine=frequency=523:sample_rate=48000:duration=1.2',
          '-c:a', 'libmp3lame', '-b:a', '256k', '-id3v2_version', '3',
          *options, str(destination)])
    return destination.read_bytes()


def _text_frames(path):
    """Read ID3v2.3/EA3 text frames independently of the application parser.

    JSymphonic's OMA contains an EA3 tag followed by the copied MP3 frames.
    This reads the on-device tag itself, not the source MP3 or JSON ledger.
    """
    data = path.read_bytes()
    assert data[:3] in (b'ID3', b'ea3'), data[:10]
    assert data[3] == 3
    result, offset = {}, 10
    while offset + 10 <= min(len(data), 64 * 1024):
        key = data[offset:offset + 4]
        if not re.fullmatch(rb'[A-Z0-9]{4}', key):
            break
        size = int.from_bytes(data[offset + 4:offset + 8], 'big')
        assert size > 0 and offset + 10 + size <= len(data)
        value = data[offset + 10:offset + 10 + size]
        if key.startswith(b'T'):
            encoding = {0: 'latin-1', 1: 'utf-16', 2: 'utf-16-be', 3: 'utf-8'}[value[0]]
            decoded = value[1:].decode(encoding).rstrip('\x00')
            result.setdefault(key.decode('ascii'), []).append(decoded)
        offset += 10 + size
    return result


@pytest.fixture(scope='module')
def evidence():
    rows = []
    yield rows
    # Optional evidence output is a test setting, never read by production.
    output = os.environ.get('NIGHTOPS_METADATA_PROOF')
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'scope': 'Fresh API + real ffmpeg + real Java mock-device metadata',
            'scanner': 'Injected test scanner; real Defender not exercised',
            'hardware': 'No physical Walkman was discovered or accessed',
            'checks_completed': rows,
        }, indent=2, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def real_context(tmp_path, monkeypatch):
    ffmpeg = os.environ.get('WALKMAN_BRIDGE_FFMPEG')
    java = os.environ.get('WALKMAN_BRIDGE_JAVA')
    assert ffmpeg and Path(ffmpeg).is_file(), 'Set a resolved WALKMAN_BRIDGE_FFMPEG'
    assert java and Path(java).is_file(), 'Set a resolved WALKMAN_BRIDGE_JAVA'
    assert jsymphonic.JAR_PATH.is_file()
    assert jsymphonic.SHIM_CMD_PREFIX is None, 'Real Java must not be replaced by a shim'
    mount = tmp_path / 'fresh-mock-walkman'
    (mount / 'OMGAUDIO').mkdir(parents=True)
    scanner = Scanner()
    scanner.scan_artwork = scanner.scan_audio
    checked_calls = []

    def assert_cleared(path):
        assert path in scanner.calls, f'Consumer saw unscanned bytes: {path.name}'
        store = app.state.store
        record = store.get(path.parent.name)
        artifact = next(value for value in record['artifacts'].values() if value['name'] == path.name)
        assert scanner.clearance_valid(path, artifact['scan'])

    analyze = media_service.analyze_cleared_audio
    convert = media_service.convert_managed
    extract = media_service.extract_cleared_artwork
    add = jsymphonic.add_tracks

    def checked_analyze(path):
        assert_cleared(path)
        checked_calls.append('real_analysis_after_clearance')
        return analyze(path)

    def checked_convert(src, out, kind, max_bytes, metadata=None):
        assert_cleared(src)
        checked_calls.append('real_conversion_after_clearance')
        return convert(src, out, kind, max_bytes, metadata=metadata)

    def checked_extract(src, out):
        assert_cleared(src)
        checked_calls.append('real_artwork_extraction_after_clearance')
        return extract(src, out)

    def checked_add(target, paths, callback):
        assert target == mount
        assert mount.is_relative_to(tmp_path)
        for path in paths:
            assert_cleared(path)
        checked_calls.append('real_java_after_derivative_clearance')
        return add(target, paths, callback)

    monkeypatch.setattr(media_service, 'analyze_cleared_audio', checked_analyze)
    monkeypatch.setattr(media_service, 'convert_managed', checked_convert)
    monkeypatch.setattr(media_service, 'extract_cleared_artwork', checked_extract)
    api = SimpleNamespace(find_walkman=lambda: mount,
        capture_device_identity=device.capture_device_identity,
        device_info=device.device_info, backup_device=device.backup_device,
        list_tracks=jsymphonic.list_tracks, add_tracks=checked_add,
        remove_track=jsymphonic.remove_track, JSymphonicError=jsymphonic.JSymphonicError)
    app = create_app(product='bridge', data_dir=tmp_path / 'state',
        token=TOKEN, origin=ORIGIN, scanner=scanner, device_api=api)
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        yield SimpleNamespace(client=client, app=app, scanner=scanner,
            mount=mount, tmp=tmp_path, ffmpeg=ffmpeg, checked_calls=checked_calls)


def _import(context, name, content):
    response = context.client.post('/api/media/import',
        files={'files': (name, content, 'audio/mpeg')})
    assert response.status_code == 200, response.text
    job = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    media_id = job['files'][0]['media_id']
    item = next(item for item in context.client.get('/api/queue').json()['items'] if item['id'] == media_id)
    return media_id, item


def _transfer(context, media_id):
    response = context.client.post('/api/transfers', json={'media_ids': [media_id]})
    assert response.status_code == 200, response.text
    job = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    assert job['files'][0]['state'] == 'transferred', job
    tracks = context.client.get('/api/tracks?refresh=1')
    assert tracks.status_code == 200, tracks.text
    assert len(tracks.json()) == 1
    oma = list((context.mount / 'OMGAUDIO').rglob('*.OMA'))
    assert len(oma) == 1
    store = context.app.state.store
    transfer = store.path(store.get(media_id), 'transfer')
    assert len(list((context.mount / 'OMGAUDIO').glob('*.DAT'))) == 17
    assert 'real_java_after_derivative_clearance' in context.checked_calls
    assert context.client.get('/api/queue').json()['items'][0]['id'] == media_id
    assert not context.client.get('/api/engine-busy').json()['busy']
    return tracks.json()[0], transfer, oma[0]


def test_real_tagged_metadata_reaches_walkman_oma(real_context, evidence):
    context = real_context
    tags = {'title': 'Étoile du soir', 'artist': 'Marie & Jean',
            'album': 'Les nuits', 'genre': 'Jazz', 'date': '2024', 'track': '7/12'}
    content = _generate(context.ffmpeg, context.tmp / 'tagged.mp3', tags)
    media_id, item = _import(context, 'misleading filename.mp3', content)
    assert {key: item[key] for key in tags} == tags
    assert item['year'] == 2024
    track, transfer, oma = _transfer(context, media_id)
    for key in ('title', 'artist', 'album'):
        assert track[key] == tags[key]
    mp3, device_tags = _text_frames(transfer), _text_frames(oma)
    for frame, key in [('TIT2', 'title'), ('TPE1', 'artist'), ('TALB', 'album'), ('TCON', 'genre')]:
        assert mp3[frame] == device_tags[frame] == [tags[key]]
    assert mp3['TYER'] == device_tags['TYER'] == ['2024']
    assert mp3['TRCK'] == ['7/12']
    assert 'OMG_TRACK\x007' in device_tags['TXXX']
    evidence.append({'test': 'tagged_six_fields', 'passed': True,
        'api_metadata': {**tags, 'year': item['year']},
        'mp3_frames': mp3, 'oma_frames': device_tags,
        'java_ledger': track, 'clearance_order': context.checked_calls})


def test_real_untagged_original_name_reaches_walkman(real_context, evidence):
    context = real_context
    content = _generate(context.ffmpeg, context.tmp / 'untagged.mp3')
    media_id, item = _import(context, 'Evening rehearsal.mp3', content)
    expected = {'title': 'Evening rehearsal', 'artist': 'Unknown artist', 'album': 'Unknown album'}
    assert {key: item[key] for key in expected} == expected
    track, transfer, oma = _transfer(context, media_id)
    assert {key: track[key] for key in expected} == expected
    mp3, device_tags = _text_frames(transfer), _text_frames(oma)
    for frame, key in [('TIT2', 'title'), ('TPE1', 'artist'), ('TALB', 'album')]:
        assert mp3[frame] == device_tags[frame] == [expected[key]]
    assert media_id not in json.dumps(device_tags)
    evidence.append({'test': 'untagged_original_filename', 'passed': True,
        'expected': expected, 'mp3_frames': mp3, 'oma_frames': device_tags,
        'clearance_order': context.checked_calls})


def test_legacy_transfer_cache_is_regenerated_before_java(real_context, evidence):
    context = real_context
    content = _generate(context.ffmpeg, context.tmp / 'source.mp3')
    media_id, _ = _import(context, 'Recovered session.mp3', content)
    store = context.app.state.store
    record = store.get(media_id)
    stale = store.path(record).parent / 'transfer.mp3'
    _generate(context.ffmpeg, stale, {'title': 'transfer', 'artist': media_id, 'album': 'cache'})
    previous_hash = hashlib.sha256(stale.read_bytes()).hexdigest()
    # Reproduce persisted pre-upgrade media and the already-cleared old derivative.
    record['artifacts']['transfer'] = {'name': stale.name, 'size_bytes': stale.stat().st_size,
        'sha256': previous_hash, 'scan': context.scanner.scan_audio(stale).to_dict()}
    store.update(media_id, artifacts=record['artifacts'], metadata_policy=None,
        analyzed=True, title='source', artist='', album='')
    before_conversions = context.checked_calls.count('real_conversion_after_clearance')
    track, transfer, oma = _transfer(context, media_id)
    new_hash = hashlib.sha256(transfer.read_bytes()).hexdigest()
    assert new_hash != previous_hash
    assert context.checked_calls.count('real_conversion_after_clearance') == before_conversions + 1
    expected = {'title': 'Recovered session', 'artist': 'Unknown artist', 'album': 'Unknown album'}
    assert {key: track[key] for key in expected} == expected
    device_tags = _text_frames(oma)
    assert device_tags['TIT2'] == ['Recovered session']
    assert device_tags['TPE1'] == ['Unknown artist']
    assert device_tags['TALB'] == ['Unknown album']
    assert media_id not in json.dumps(device_tags)
    refreshed = store.get(media_id)
    assert refreshed['artifacts']['transfer']['scan']['sha256'] == new_hash
    evidence.append({'test': 'legacy_cached_transfer_regenerated', 'passed': True,
        'previous_sha256': previous_hash, 'regenerated_sha256': new_hash,
        'oma_frames': device_tags, 'clearance_order': context.checked_calls})


def test_real_embedded_artwork_api_requires_unchanged_cleared_jpeg(real_context, evidence):
    context = real_context
    cover, audio = context.tmp / 'cover.png', context.tmp / 'with-cover.mp3'
    _run([context.ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
          '-i', 'color=c=maroon:size=1024x768', '-frames:v', '1', str(cover)])
    _run([context.ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
          '-i', 'sine=frequency=523:duration=0.5', '-i', str(cover),
          '-map', '0:a', '-map', '1:v', '-codec:a', 'libmp3lame',
          '-codec:v', 'copy', '-id3v2_version', '3',
          '-disposition:v:0', 'attached_pic', str(audio)])
    media_id, item = _import(context, 'Cover recording.mp3', audio.read_bytes())
    assert item['artwork_status'] == 'ready', item
    assert 'real_artwork_extraction_after_clearance' in context.checked_calls
    route = item['artwork_url']
    response = context.client.get(route)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/jpeg'
    assert response.content.startswith(b'\xff\xd8') and response.content.endswith(b'\xff\xd9')
    assert len(response.content) < 1024 * 1024
    store = context.app.state.store
    record = store.get(media_id)
    artwork = store.path(record, 'artwork')
    assert artwork in context.scanner.calls
    assert hashlib.sha256(response.content).hexdigest() == record['artifacts']['artwork']['scan']['sha256']
    context.client.headers.pop('X-NightOps-Token')
    assert context.client.get(route).status_code == 403
    context.client.headers['X-NightOps-Token'] = TOKEN
    artwork.write_bytes(response.content + b'changed')
    assert context.client.get(route).status_code == 423
    assert store.get(media_id)['artwork_status'] == 'needs_scan'
    assert not context.client.get('/api/engine-busy').json()['busy']
    evidence.append({'test': 'real_embedded_artwork_authenticated_hash_gate', 'passed': True,
        'served_bytes': len(response.content), 'unauthenticated_status': 403,
        'tampered_artwork_status': 423, 'clearance_order': context.checked_calls})


def test_link_boundary_provider_tags_reach_real_java(real_context, monkeypatch, evidence):
    import link_import
    context = real_context
    content = _generate(context.ffmpeg, context.tmp / 'provider-audio.mp3')
    hints = {'title': 'Provider track', 'artist': 'Provider artist',
             'album': 'Provider album', 'genre': 'Rock', 'year': 2023, 'track': 4}

    def download(url, folder, max_bytes, progress=None):
        assert url == 'https://soundcloud.com/test-artist/test-track'
        assert len(content) < max_bytes
        target = folder / 'download.mp3'
        target.write_bytes(content)
        if progress:
            progress({'downloaded_bytes': len(content), 'total_bytes': len(content)})
        return link_import.DownloadedMedia(target, 'Remote download.mp3', hints)

    monkeypatch.setattr(link_import, 'download_link', download)
    response = context.client.post('/api/media/import-link',
        json={'url': 'https://soundcloud.com/test-artist/test-track'})
    assert response.status_code == 200, response.text
    job = context.client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'done', job
    assert job['kind'] == 'link_import'
    media_id = job['files'][0]['media_id']
    assert job['files'][0]['downloaded_bytes'] == len(content)
    assert not list(context.app.state.store.root.glob('download-*'))
    track, _, oma = _transfer(context, media_id)
    for key in ('title', 'artist', 'album'):
        assert track[key] == hints[key]
    frames = _text_frames(oma)
    assert frames['TCON'] == ['Rock']
    assert frames['TYER'] == ['2023']
    assert 'OMG_TRACK\x004' in frames['TXXX']
    evidence.append({'test': 'provider_metadata_api_real_conversion_java', 'passed': True,
        'network': 'Downloader boundary injected; no live provider download',
        'oma_frames': frames, 'clearance_order': context.checked_calls})
