"""Versioned packaged management acceptance using real HTTP, Defender and runtimes.

Only the device boundary is replaced: Bridge is explicitly pinned through
MOCK_DEVICE_PATH to a private copy of an existing backup. There is no scanner,
decoder, metadata or Java injection, and no physical-drive discovery.
The driver and the reused HTTP helper use Python's standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import wave
from urllib.error import HTTPError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
ENGINE_SHA256 = 'cbab7deae5baed02b799f43a454095e5b8b8d00a26151cc0c8649c2cccbcfc72'
spec = importlib.util.spec_from_file_location('packaged_media_probe',
    Path(__file__).with_name('probe-packaged-media.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


INVENTORY = r'''
import importlib.util, json, os, pathlib, shutil, sys
import application, playlists, media_service, metadata, scan, fastapi
root = pathlib.Path.cwd().resolve()
modules = {m.__name__: str(pathlib.Path(m.__file__).resolve())
    for m in (application, playlists, media_service, metadata, scan, fastapi)}
assert all(pathlib.Path(p).is_relative_to(root) for p in modules.values())
product = os.environ['NIGHTOPS_PRODUCT']
result = {'product': product, 'python': sys.version.split()[0],
 'isolated': sys.flags.isolated, 'executable': sys.executable, 'modules': modules,
 'developer_tools_on_path': {name: shutil.which(name) for name in
    ('java','javac','python','python3','py','node','npm','dotnet','git','ffmpeg')}}
if product == 'player':
 assert importlib.util.find_spec('device') is None
 assert importlib.util.find_spec('jsymphonic') is None
 assert 'device' not in sys.modules and 'jsymphonic' not in sys.modules
 result['device_modules_absent'] = True
else:
 import device, jsymphonic
 expected = pathlib.Path(os.environ['MOCK_DEVICE_PATH']).resolve(strict=True)
 actual = device.find_walkman()
 assert actual and actual.resolve() == expected
 assert expected.name == 'bridge-device' and len(expected.parts) > 3
 assert pathlib.Path(device.__file__).resolve().is_relative_to(root)
 assert pathlib.Path(jsymphonic.__file__).resolve().is_relative_to(root)
 result['device_selection'] = str(actual)
 result['device_discovery_replaced_by_explicit_fixture'] = True
print(json.dumps(result))
'''


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def file_hashes(root):
    root = root.resolve(strict=True)
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Fixture paths must not contain reparse points')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = helper.digest(path)
    return result


def header(headers, name):
    return next((value for key, value in headers.items() if key.lower() == name.lower()), None)


class Backend(helper.Backend):
    def __init__(self, resources, state, product, fixture=None):
        super().__init__(resources, state)
        self.product = product
        self.env['NIGHTOPS_PRODUCT'] = product
        if product == 'bridge':
            check(fixture and fixture.resolve().name == 'bridge-device', 'Explicit copied Sony fixture required')
            self.env['MOCK_DEVICE_PATH'] = str(fixture.resolve(strict=True))
            self.env['WALKMAN_BRIDGE_JAVA'] = str(resources / 'jre' / 'bin' / 'java.exe')

    def start(self):
        self.process = subprocess.Popen([str(self.resources / 'python' / 'python.exe'),
            '-I', '-B', '-m', 'boot'], cwd=self.resources / 'backend', env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        def lines():
            for line in self.process.stdout:
                if line.startswith('NIGHTOPS_READY '):
                    self.events.put(line)
        threading.Thread(target=lines, daemon=True).start()
        try:
            announced = json.loads(self.events.get(timeout=30).removeprefix('NIGHTOPS_READY '))
        except queue.Empty:
            raise RuntimeError('Packaged backend did not announce readiness') from None
        port = announced['port']
        check(type(port) is int and 1 <= port <= 65535, 'Invalid readiness port')
        proof = hmac.new(self.token.encode(), str(port).encode(), hashlib.sha256).hexdigest()
        check(hmac.compare_digest(proof, announced['proof']), 'Readiness authentication failed')
        self.origin = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                status, _, health = self.call('/api/health')
                if status == 200:
                    check(health['product'] == self.product and health['version'] == VERSION, 'Packaged product/version mismatch')
                    expected_device = self.product == 'bridge'
                    check(health['java'] == expected_device and health['jar'] == expected_device,
                        'Packaged Java isolation mismatch')
                    check(health['device_connected'] == expected_device, 'Unexpected selected device state')
                    check(self.call('/api/health', authenticated=False)[0] == 403, 'Unauthenticated request accepted')
                    return health
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError('Packaged backend health did not become ready')

    def upload(self, sources):
        boundary = 'management-proof-' + os.urandom(12).hex()
        parts = []
        for source in sources:
            parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{source.name}"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                source.read_bytes(), b'\r\n'])
        parts.append(f'--{boundary}--\r\n'.encode())
        request = Request(self.origin + '/api/media/import', data=b''.join(parts), method='POST',
            headers={'X-NightOps-Token': self.token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            response = self.opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            body = json.loads(response.read(1024 * 1024))
            check(response.status == 200, f'WAV import failed: {body}')
        return body


def expect(backend, route, method='GET', payload=None, *, status=200, headers=None):
    actual, response_headers, result = backend.call(route, method, payload, headers=headers)
    check(actual == status, f'{method} {route} returned {actual}, expected {status}: {result}')
    return result, response_headers


def media_record(state, media_id):
    path = (state / 'nightops.sqlite').resolve()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as database:
        row = database.execute('SELECT record FROM media WHERE id=?', (media_id,)).fetchone()
        check(row is not None, 'Expected retained media record')
        return json.loads(row[0])


def source_path(state, media_id):
    record = media_record(state, media_id)
    return state / 'cache' / media_id / record['artifacts']['source']['name']


def create_wave(path, frequency):
    samples = b''.join(struct.pack('<h', int(5000 * math.sin(2 * math.pi * frequency * n / 44100)))
        for n in range(44100))
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(samples)


def local_before_restart(backend, sources, report):
    submitted = backend.upload(sources)
    job, phases = backend.job(submitted['job_id'])
    report['import'] = {'job': job, 'phases': phases}
    check(job['status'] == 'done', 'Real Defender import did not complete cleanly')
    library, _ = expect(backend, '/api/queue')
    check(len(library['items']) == 2, 'Import did not retain two managed files')
    ids = [row['id'] for row in library['items']]
    for media_id, source in zip(ids, sources):
        check(helper.digest(source_path(backend.state, media_id)) == helper.digest(source), 'Imported bytes differ from source')
    report['source_scans'] = [helper.record_scan(backend.state, media_id, 'source') for media_id in ids]
    edited, _ = expect(backend, f'/api/media/{ids[0]}/metadata', 'PATCH', {
        'title': 'Packaged ' + chr(0x65e5) + chr(0x672c), 'artist': 'Acceptance Artist',
        'album': 'Management Proof', 'genre': 'Test Tone', 'year': '2026', 'track': '1/2'})
    check(edited['metadata_revision'] == 1 and edited['year'] == 2026, 'Metadata revision or year was not saved')
    check('metadata_overrides' not in edited and 'artifacts' not in edited, 'Private metadata leaked into the HTTP object')
    check(helper.digest(source_path(backend.state, ids[0])) == helper.digest(sources[0]), 'Metadata edit changed source bytes')
    report['metadata'] = {key: edited.get(key) for key in ('title', 'artist', 'album', 'genre', 'year', 'track', 'metadata_revision')}
    made, _ = expect(backend, '/api/playlists', 'POST', {'name': 'Packaged night', 'media_ids': ids[::-1]}, status=201)
    reordered, _ = expect(backend, '/api/playlists/' + made['id'], 'PATCH',
        {'name': 'Saved night', 'media_ids': ids}, headers={'If-Match': made['etag']})
    expect(backend, '/api/playlists/' + made['id'], 'PATCH', {'name': 'Stale overwrite'},
        status=409, headers={'If-Match': made['etag']})
    saved, _ = expect(backend, '/api/playback-state', 'PATCH',
        {'playlist_id': made['id'], 'media_id': ids[1], 'position_seconds': 0.25, 'volume': 0.2})
    check(saved['playlist']['media_ids'] == ids, 'Saved playlist scope does not match membership')
    report['playlist_before_restart'] = reordered
    return ids, reordered


def local_after_restart(backend, ids, playlist, sources, report):
    restored, _ = expect(backend, '/api/playlists/' + playlist['id'])
    check(restored == playlist, 'Playlist changed across packaged backend restart')
    playback, _ = expect(backend, '/api/playback-state')
    check(playback['playlist_id'] == playlist['id'] and playback['playlist'] == playlist,
        'Playlist scope did not survive restart')
    check(playback['media_id'] == ids[1] and playback['position_seconds'] == 0.25,
        'Saved playback selection/position did not survive restart')
    report['restored_playback'] = playback
    member_source = source_path(backend.state, ids[1])
    reduced, _ = expect(backend, '/api/playlists/' + playlist['id'], 'PATCH',
        {'media_ids': [ids[0]]}, headers={'If-Match': restored['etag']})
    check(member_source.exists() and helper.digest(member_source) == helper.digest(sources[1]),
        'Membership deletion removed audio bytes')
    expect(backend, '/api/playlists/' + playlist['id'], 'DELETE', headers={'If-Match': reduced['etag']})
    library, _ = expect(backend, '/api/queue')
    check([row['id'] for row in library['items']] == ids, 'Playlist deletion removed library audio')
    playback, _ = expect(backend, '/api/playback-state')
    check(playback['playlist_id'] is None and playback['playlist'] is None, 'Deleted playlist scope remained persisted')
    pruning, _ = expect(backend, '/api/playlists', 'POST', {'name': 'Pruning proof', 'media_ids': ids}, status=201)
    expect(backend, '/api/queue/items/' + ids[1], 'DELETE')
    pruned, _ = expect(backend, '/api/playlists/' + pruning['id'])
    check(pruned['media_ids'] == [ids[0]] and pruned['revision'] > pruning['revision'],
        'Library deletion did not prune saved membership')
    check(not member_source.exists(), 'Unleased removed managed bytes remain')
    check(sources[1].exists(), 'Original generated WAV was removed')
    expect(backend, '/api/playlists/' + pruning['id'], 'DELETE', headers={'If-Match': pruned['etag']})
    report['file_safety'] = {'membership_delete_keeps_audio': True, 'playlist_delete_keeps_audio': True,
        'library_delete_prunes_membership': True, 'originals_retained': True}


def native_management(backend, media_id, fixture, report):
    device, _ = expect(backend, '/api/device')
    check(Path(device['mount_path']).resolve() == fixture.resolve(), 'Backend selected a device other than the private copy')
    tracks, _ = expect(backend, '/api/tracks')
    before_ids = {row['id'] for row in tracks}
    check(len(tracks) >= 2, 'Copied Sony fixture needs two tracks')
    chosen = [row['id'] for row in tracks[:2]]
    _, response_headers = expect(backend, '/api/device/playlists')
    initial_etag = header(response_headers, 'etag')
    name = 'Probe ' + chr(0x65e5) + chr(0x672c) + ' "QA"'
    made, _ = expect(backend, '/api/device/playlists', 'POST', {'name': name, 'track_ids': chosen},
        status=201, headers={'If-Match': initial_etag})
    check(made['playlist']['name'] == name, 'Packaged native Unicode transport corrupted the name')
    playlist_id = made['playlist']['id']
    native_job, _ = expect(backend, '/api/jobs/' + made['job_id'])
    check(native_job['status'] == 'done' and native_job['progress'] == 1 and not native_job['needs_reconcile'],
        'Native create did not retain truthful terminal job fields')
    expect(backend, '/api/device/playlists/' + playlist_id, 'PATCH', {'name': 'Stale'},
        status=409, headers={'If-Match': initial_etag})
    _, response_headers = expect(backend, '/api/device/playlists')
    updated, _ = expect(backend, '/api/device/playlists/' + playlist_id, 'PATCH',
        {'name': name + ' saved', 'track_ids': chosen[::-1]}, headers={'If-Match': header(response_headers, 'etag')})
    check(updated['playlist']['track_ids'] == chosen[::-1], 'Native playlist order was not saved')
    submitted, _ = expect(backend, '/api/transfers', 'POST', {'media_ids': [media_id]})
    job, phases = backend.job(submitted['job_id'])
    report['transfer'] = {'job': job, 'phases': phases}
    check(job['status'] == 'done' and job['files'][0]['state'] == 'transferred', 'Packaged generated-track transfer failed')
    report['transfer']['scan'] = helper.record_scan(backend.state, media_id, 'transfer')
    record = media_record(backend.state, media_id)
    check(record['artifacts']['transfer']['metadata_revision'] == record['metadata_revision'],
        'Packaged transfer derivative carries stale metadata')
    after, _ = expect(backend, '/api/tracks?refresh=1')
    added = [row for row in after if row['id'] not in before_ids]
    check(len(added) == 1 and len(after) == len(tracks) + 1, 'Transfer did not add exactly one copied-device track')
    check(added[0]['title'] == report['metadata']['title'], 'Transferred Sony title differs from edited metadata')
    native, response_headers = expect(backend, '/api/device/playlists')
    survived = next(row for row in native['items'] if row['id'] == playlist_id)
    check(survived['track_ids'] == chosen[::-1], 'Ordinary transfer changed native playlist membership')
    final_ids = [chosen[0], chosen[0], added[0]['id']]
    updated, _ = expect(backend, '/api/device/playlists/' + playlist_id, 'PATCH', {'track_ids': final_ids},
        headers={'If-Match': header(response_headers, 'etag')})
    check(updated['playlist']['track_ids'] == final_ids, 'Native repeated songs were lost')
    _, response_headers = expect(backend, '/api/device/playlists')
    deleted, _ = expect(backend, '/api/device/playlists/' + playlist_id, 'DELETE',
        headers={'If-Match': header(response_headers, 'etag')})
    remaining, _ = expect(backend, '/api/tracks?refresh=1')
    check({row['id'] for row in remaining} == {row['id'] for row in after}, 'Native playlist deletion removed device audio')
    report['native'] = {'fixture_selected': str(fixture), 'created_name': name, 'create_job': native_job,
        'delete_job_id': deleted['job_id'], 'native_order_and_repeats_preserved': True,
        'stale_snapshot_rejected': True, 'playlist_delete_keeps_device_tracks': True,
        'tracks_before': len(tracks), 'tracks_after': len(remaining), 'added_track': added[0]}


def run_product(resources, product, run_root, sources, fixture):
    state = run_root / (product + '-state')
    state.mkdir()
    backend = Backend(resources, state, product, fixture if product == 'bridge' else None)
    result = {'product': product, 'resources': str(resources), 'state': str(state), 'passed': False,
        'phase': 'inventory', 'cleanup': []}
    try:
        inventory = subprocess.run([str(resources / 'python' / 'python.exe'), '-I', '-B', '-c', INVENTORY],
            cwd=resources, env=backend.env, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        check(inventory.returncode == 0, 'Packaged inventory failed: ' + inventory.stderr[-1200:])
        result['inventory'] = json.loads(inventory.stdout)
        check(result['inventory']['isolated'] == 1, 'Packaged Python is not isolated')
        check(not any(result['inventory']['developer_tools_on_path'].values()), 'Developer tools were available on PATH')
        result['phase'] = 'start'
        result['health'] = backend.start()
        if product == 'player':
            expect(backend, '/api/device/playlists', status=404)
        result['phase'] = 'import_metadata_playlist'
        ids, playlist = local_before_restart(backend, sources, result)
        result['cleanup'].append(backend.close())
        check(result['cleanup'][-1].get('drained'), 'Backend did not drain before restart')
        backend = Backend(resources, state, product, fixture if product == 'bridge' else None)
        result['phase'] = 'restart_restore_file_safety'
        backend.start()
        local_after_restart(backend, ids, playlist, sources, result)
        if product == 'bridge':
            result['phase'] = 'native_crud_and_transfer_to_private_copy'
            native_management(backend, ids[0], fixture, result)
        result['passed'] = True
        result['phase'] = 'complete'
    except Exception as exc:
        result['failure_type'] = type(exc).__name__
        result['failure'] = str(exc)[:2500]
    finally:
        try:
            result['cleanup'].append(backend.close())
        except Exception as exc:
            result['cleanup'].append({'drained': False, 'failure_type': type(exc).__name__,
                'backend_pid': backend.process.pid if backend.process else None,
                'left_running': bool(backend.process and backend.process.poll() is None)})
        result['passed'] = result['passed'] and all(row.get('drained') for row in result['cleanup'])
    print(json.dumps({'event': 'product_complete', 'product': product, 'passed': result['passed'],
        'phase': result['phase'], 'failure': result.get('failure')}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--player-resources', type=Path, default=ROOT / 'dist_electron/player/win-unpacked/resources')
    parser.add_argument('--bridge-resources', type=Path, default=ROOT / 'dist_electron/bridge/win-unpacked/resources')
    parser.add_argument('--backup', type=Path, default=ROOT / 'hardware-proof/pre-playlist-backups/2026-09-05-194948')
    parser.add_argument('--output', type=Path, default=ROOT / 'packaging/build/management-upgrade/packaged-management.json')
    args = parser.parse_args()
    backup = args.backup.resolve(strict=True)
    approved_backups = (ROOT / 'hardware-proof/pre-playlist-backups').resolve(strict=True)
    check(backup.is_relative_to(approved_backups) and backup != approved_backups,
        'Only a saved workspace backup folder may be copied; physical mounts are forbidden')
    before = file_hashes(backup)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix='packaged-management-', dir=args.output.parent.resolve()))
    fixture = run_root / 'bridge-device'
    shutil.copytree(backup, fixture)
    check(file_hashes(fixture) == before, 'Private Sony copy does not match backup')
    source_dir = run_root / 'sources'
    source_dir.mkdir()
    sources = [source_dir / 'Proof One.wav', source_dir / 'Proof Two.wav']
    for path, frequency in zip(sources, (440, 660)):
        create_wave(path, frequency)
    original_hashes = {path.name: helper.digest(path) for path in sources}
    report = {'passed': False, 'version': VERSION, 'scope': 'Packaged Player and Bridge management with real Defender; private Sony backup copy only',
        'run_root': str(run_root), 'backup': str(backup), 'backup_files': len(before),
        'original_source_hashes': original_hashes, 'products': []}
    try:
        for product, supplied in (('player', args.player_resources), ('bridge', args.bridge_resources)):
            resources = supplied.resolve(strict=True)
            check(json.loads((resources / 'product.json').read_text())['product'] == product, 'Wrong packaged product resources')
            if product == 'player':
                check(not (resources / 'jre').exists() and not list(resources.rglob('*.jar')), 'Player unexpectedly bundles Java')
                check(not (resources / 'backend/device.py').exists() and not (resources / 'backend/jsymphonic.py').exists(),
                    'Player unexpectedly bundles device modules')
            else:
                check((resources / 'jre/bin/java.exe').is_file(), 'Bridge is missing its packaged JRE')
                check(helper.digest(resources / 'backend/vendor/jsymphonic.jar') == ENGINE_SHA256, 'Packaged engine differs from reviewed candidate')
                report['engine_sha256'] = ENGINE_SHA256
            report['products'].append(run_product(resources, product, run_root, sources, fixture))
        report['passed'] = len(report['products']) == 2 and all(row['passed'] for row in report['products'])
    except Exception as exc:
        report['failure_type'], report['failure'] = type(exc).__name__, str(exc)[:2500]
    finally:
        report['backup_unchanged'] = file_hashes(backup) == before
        report['originals_unchanged'] = {path.name: helper.digest(path) for path in sources} == original_hashes
        report['passed'] = report['passed'] and report['backup_unchanged'] and report['originals_unchanged']
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'output': str(args.output),
        'backup_unchanged': report['backup_unchanged'], 'originals_unchanged': report['originals_unchanged']}), flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
