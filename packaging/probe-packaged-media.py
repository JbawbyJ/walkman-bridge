"""Operator probe of packaged Player: real public download, Defender and streaming.

Uses only stdlib in the driver and only bundled runtimes in the application.
No injected scanner, decoder, downloader or device boundary. Bridge is inspected
as files only; no physical device discovery or write is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


INVENTORY = r'''
import importlib.metadata, json, pathlib, shutil, subprocess, sys
import fastapi, yt_dlp, yt_dlp_ejs, link_import
root = pathlib.Path.cwd().resolve()
modules = {module.__name__: str(pathlib.Path(module.__file__).resolve())
           for module in (fastapi, yt_dlp, yt_dlp_ejs, link_import)}
assert all(pathlib.Path(path).is_relative_to(root) for path in modules.values())
deno = link_import._fixed_deno_path()
assert deno.is_relative_to(root)
result = subprocess.run([str(deno), '--version'], capture_output=True, text=True,
                        timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
assert result.returncode == 0
print(json.dumps({'python':sys.version.split()[0], 'isolated':sys.flags.isolated,
 'executable':sys.executable, 'modules':modules, 'deno_path':str(deno),
 'deno_version':result.stdout.splitlines(),
 'yt_dlp':importlib.metadata.version('yt-dlp'),
 'yt_dlp_ejs':importlib.metadata.version('yt-dlp-ejs'),
 'developer_tools_on_path':{name:shutil.which(name) for name in
    ('java','javac','python','python3','py','node','npm','dotnet','git','ffmpeg','deno')}}))
'''


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def controlled_environment(resources, state, token):
    allowed = ('SYSTEMROOT', 'WINDIR', 'PROGRAMFILES', 'PROGRAMDATA',
               'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'TEMP', 'TMP',
               'COMSPEC', 'PATHEXT', 'PROCESSOR_ARCHITECTURE', 'SYSTEMDRIVE')
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    system = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32'
    env.update(PATH=str(system), NIGHTOPS_PRODUCT='player', NIGHTOPS_TOKEN=token,
        NIGHTOPS_DATA_DIR=str(state), WALKMAN_JOBS_DB=str(state / 'nightops.sqlite'),
        WALKMAN_BRIDGE_FFMPEG=str(resources / 'ffmpeg' / 'ffmpeg.exe'),
        PYTHONUTF8='1', PYTHONIOENCODING='utf-8', DENO_NO_UPDATE_CHECK='1')
    return env


class Backend:
    def __init__(self, resources, state):
        self.resources, self.state = resources, state
        self.token = secrets.token_urlsafe(48)
        self.env = controlled_environment(resources, state, self.token)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.process = None
        self.origin = None
        self.events = queue.Queue()

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
            line = self.events.get(timeout=20)
        except queue.Empty:
            raise RuntimeError('Packaged backend did not announce readiness') from None
        message = json.loads(line.removeprefix('NIGHTOPS_READY '))
        port = message['port']
        assert isinstance(port, int) and 1 <= port <= 65535
        proof = hmac.new(self.token.encode(), str(port).encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(proof, message['proof']), 'Readiness authentication failed'
        self.origin = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 20
        while True:
            try:
                status, _, health = self.call('/api/health')
                if status == 200:
                    assert health['product'] == 'player' and health['version'] == json.loads((self.resources / 'runtime-manifest.json').read_text(encoding='utf-8'))['version']
                    assert not health['java'] and not health['jar'] and not health['device_connected']
                    assert self.call('/api/health', authenticated=False)[0] == 403
                    return health
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('Packaged backend health did not become ready')
            time.sleep(0.1)

    def call(self, route, method='GET', payload=None, *, headers=None,
             raw=False, authenticated=True):
        assert route.startswith('/api/') and self.origin
        outgoing = {'X-NightOps-Token': self.token} if authenticated else {}
        if headers:
            outgoing.update(headers)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            outgoing['Content-Type'] = 'application/json'
        request = Request(self.origin + route, data=data, headers=outgoing, method=method)
        try:
            response = self.opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            content = response.read(2 * 1024 * 1024 + 1)
            assert len(content) <= 2 * 1024 * 1024
            return response.status, dict(response.headers), content if raw else json.loads(content)

    def job(self, job_id):
        deadline = time.monotonic() + 720
        phases = []
        while True:
            status, _, job = self.call('/api/jobs/' + job_id)
            assert status == 200
            phase = job.get('phase')
            if not phases or phases[-1] != phase:
                phases.append(phase)
                print(json.dumps({'event': 'job_phase', 'kind': job.get('kind'), 'phase': phase}), flush=True)
            if job['status'] in ('done', 'partial', 'failed', 'interrupted'):
                return job, phases
            if any(row.get('state') == 'awaiting_permission' for row in job.get('files', [])):
                # This operator probe has no Electron UAC broker. Cancel only its
                # own pending exchange and report unavailable acceptance honestly.
                _, _, pending = self.call('/api/internal/scanner/pending')
                for request in pending:
                    self.call('/api/internal/scanner/cancel', 'POST', {
                        'request_id': request['request_id'], 'reason_code': 'elevation_cancelled'})
            if time.monotonic() > deadline:
                raise RuntimeError('Packaged job exceeded the operator probe deadline')
            time.sleep(0.25)

    def close(self):
        if not self.process:
            return {'launched': False}
        if self.process.poll() is not None:
            return {'already_exited': True, 'exit_code': self.process.returncode}
        if not self.origin:
            self.process.terminate()
            self.process.wait(timeout=15)
            return {'terminated_before_ready': True}
        self.call('/api/shutdown/drain', 'POST')
        deadline = time.monotonic() + 720
        while True:
            _, _, busy = self.call('/api/engine-busy')
            if not busy['busy']:
                self.process.terminate()
                self.process.wait(timeout=15)
                return {'drained': True, 'busy_before_termination': False,
                        'backend_pid': self.process.pid, 'exit_code': self.process.returncode}
            if time.monotonic() > deadline:
                return {'drained': False, 'left_running': True, 'backend_pid': self.process.pid}
            time.sleep(0.5)


def record_scan(state, media_id, kind):
    with sqlite3.connect(state / 'nightops.sqlite') as database:
        record = json.loads(database.execute('SELECT record FROM media WHERE id=?', (media_id,)).fetchone()[0])
    artifact = record['artifacts'][kind]
    scan = artifact['scan']
    path = state / 'cache' / media_id / artifact['name']
    assert scan['ok'] and scan['state'] == 'CLEAN'
    assert scan['sha256'] == artifact['sha256'] == digest(path)
    assert scan['size_bytes'] == path.stat().st_size
    assert scan['defender_signature_version'] and scan['defender_engine_version']
    return scan


def probe_link(backend, url):
    status, _, submitted = backend.call('/api/media/import-link', 'POST', {'url': url})
    assert status == 200, submitted
    job, phases = backend.job(submitted['job_id'])
    result = {'url': url, 'job_status': job['status'], 'phases': phases,
              'files': job['files'], 'passed': False}
    if job['status'] != 'done':
        return result
    media_id = job['files'][0]['media_id']
    _, _, queue_data = backend.call('/api/queue')
    item = next(item for item in queue_data['items'] if item['id'] == media_id)
    assert item['title'] and item['artist'] and item['album'] and item['duration_seconds'] > 0
    result['metadata'] = {key: item.get(key) for key in
        ('name', 'title', 'artist', 'album', 'genre', 'date', 'year', 'track', 'duration_seconds')}
    result['source_scan'] = record_scan(backend.state, media_id, 'source')
    status, headers, source = backend.call(f'/api/media/{media_id}/stream', raw=True,
        headers={'Range': 'bytes=0-63'})
    assert status == 206 and len(source) == 64
    result['source_range'] = {'status': status, 'bytes': len(source)}
    status, _, submitted = backend.call(f'/api/media/{media_id}/prepare', 'POST', {'format': 'flac'})
    assert status == 200, submitted
    prepared, result['prepare_phases'] = backend.job(submitted['job_id'])
    assert prepared['status'] == 'done', prepared
    result['flac_scan'] = record_scan(backend.state, media_id, 'playback')
    status, _, flac = backend.call(f'/api/media/{media_id}/stream', raw=True,
        headers={'Range': 'bytes=0-3'})
    assert status == 206 and flac == b'fLaC'
    result['flac_range'] = {'status': status, 'magic': 'fLaC'}
    assert not list((backend.state / 'cache').glob('download-*'))
    result['download_workspace_removed'] = True
    result['passed'] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('resources', type=Path)
    parser.add_argument('--bridge-resources', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    resources = args.resources.resolve(strict=True)
    assert json.loads((resources / 'product.json').read_text())['product'] == 'player'
    assert not (resources / 'jre').exists()
    assert not (resources / 'backend' / 'device.py').exists()
    assert not (resources / 'backend' / 'jsymphonic.py').exists()
    assert not list(resources.rglob('*.jar'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix='packaged-live-state-', dir=args.output.parent.resolve()))
    backend = Backend(resources, state)
    report = {'passed': False, 'scope': 'Packaged Player live API with real Defender',
              'state': str(state), 'player_has_java': False, 'links': [], 'phase': 'inventory'}
    try:
        inventory = subprocess.run([str(resources / 'python' / 'python.exe'), '-I', '-B', '-c', INVENTORY],
            cwd=resources, env=backend.env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert inventory.returncode == 0, 'Packaged runtime inventory failed'
        report['inventory'] = json.loads(inventory.stdout)
        assert not any(report['inventory']['developer_tools_on_path'].values())
        report['phase'] = 'authenticated_backend_start'
        report['health'] = backend.start()
        for url in ('https://www.youtube.com/watch?v=jNQXAC9IVRw',
                    'https://soundcloud.com/giovannisarani/mezzo-valzer'):
            report['phase'] = 'youtube_import' if 'youtube.com' in url else 'soundcloud_import'
            report['links'].append(probe_link(backend, url))
        if args.bridge_resources:
            report['phase'] = 'bridge_file_parity'
            bridge = args.bridge_resources.resolve(strict=True)
            assert json.loads((bridge / 'product.json').read_text())['product'] == 'bridge'
            assert (bridge / 'jre' / 'bin' / 'java.exe').is_file()
            assert (bridge / 'backend' / 'vendor' / 'jsymphonic.jar').is_file()
            shared = ('backend/application.py', 'backend/link_import.py', 'backend/link_download_worker.py',
                      'backend/media_service.py', 'backend/metadata.py', 'deno/deno.exe',
                      'python/python.exe', 'ffmpeg/ffmpeg.exe')
            parity = {name: digest(resources / name) for name in shared}
            assert all(value == digest(bridge / name) for name, value in parity.items())
            report['bridge'] = {'java_and_jar_present': True, 'shared_sha256': parity,
                                'backend_not_started': True}
        report['passed'] = len(report['links']) == 2 and all(link['passed'] for link in report['links'])
        report['phase'] = 'complete'
    except Exception as exc:
        # Avoid persisting token-bearing requests, raw downloader URLs or tracebacks.
        report['failure_type'] = type(exc).__name__
        report['failure'] = 'The packaged acceptance probe did not complete; inspect its last recorded phase.'
    finally:
        try:
            report['cleanup'] = backend.close()
        except Exception as exc:
            report['cleanup'] = {'drained': False, 'status': 'unknown',
                'failure_type': type(exc).__name__,
                'backend_pid': backend.process.pid if backend.process else None,
                'left_running': bool(backend.process and backend.process.poll() is None)}
        report['passed'] = report['passed'] and bool(report['cleanup'].get('drained'))
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'output': str(args.output),
                      'link_results': [{'url': link['url'], 'passed': link['passed']} for link in report['links']]}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
