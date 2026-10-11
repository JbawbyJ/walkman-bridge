from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import uuid
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.formparsers import MultiPartException
from starlette.datastructures import UploadFile
from media_store import MediaStore, MediaError, MediaBusyError, PlaylistConflictError, MAX_FILES, locked_read, public_job
from playlists import register_local_routes, register_native_routes
from security import Boundary, LimitedMultipart
from operations import OperationCoordinator, DrainingError, BusyError
from scan_bridge import ElevationBridge, BridgeError
from jobs import JobStore, JobStatus
from media_service import MediaService
import scan

VERSION = '0.4.1'

class OrderBody(BaseModel):
    ids: list[str] = Field(max_length=200)

class TransferBody(BaseModel):
    media_ids: list[str] = Field(min_length=1, max_length=200)

class PrepareBody(BaseModel):
    format: str

class LinkImportBody(BaseModel):
    url: str = Field(min_length=1, max_length=4096)

class PlaybackLeaseBody(BaseModel):
    media_id: str

class PlaybackBody(BaseModel):
    media_id: str | None = None
    playlist_id: str | None = None
    position_seconds: float = Field(default=0, ge=0, le=864000, allow_inf_nan=False)
    volume: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    shuffle: bool = False
    repeat: Literal['off', 'all', 'one'] = 'off'

def state_directory(product):
    if os.environ.get('NIGHTOPS_DATA_DIR'):
        return Path(os.environ['NIGHTOPS_DATA_DIR'])
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share')
    return local / ('Red Lotus Player' if product == 'player' else 'Walkman Bridge')

def track_etag(identity, tracks):
    data = json.dumps([identity.volume_id, tracks], sort_keys=True, separators=(',', ':')).encode()
    return '"' + hashlib.sha256(data).hexdigest() + '"'

def parse_range(value, size):
    if value is None:
        return 0, size - 1, 200
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    bad = HTTPException(416, 'Unsatisfiable byte range', headers={'Content-Range': f'bytes */{size}'})
    if not match or not any(match.groups()) or size == 0:
        raise bad
    first, last = match.groups()
    start = int(first) if first else max(0, size - int(last))
    end = min(int(last), size - 1) if first and last else size - 1
    if start > end or start >= size:
        raise bad
    return start, end, 206

class LeasedStream(StreamingResponse):
    def __init__(self, *args, release, **kwargs):
        self.release = release
        super().__init__(*args, **kwargs)
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.to_thread(self.release)

def create_app(*, product=None, data_dir=None, token=None, origin=None, scanner=None, device_api=None):
    product = product or os.environ.get('NIGHTOPS_PRODUCT', 'bridge')
    if product not in ('player', 'bridge'):
        raise ValueError('Unknown Night Ops product')
    data_dir = Path(data_dir or state_directory(product))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / 'nightops.sqlite'
    legacy = Path(__file__).parent / 'walkman-jobs.sqlite'
    if product == 'bridge' and not db_path.exists() and legacy.is_file():
        import sqlite3
        with sqlite3.connect(str(legacy)) as source, sqlite3.connect(str(db_path)) as dest:
            source.backup(dest)
    jobs = JobStore(db_path)
    jobs.recover_interrupted()
    store = MediaStore(db_path, data_dir / 'cache')
    for media_id in jobs.uncertain_media_ids():
        try:
            store.update(media_id, needs_reconcile=True)
        except MediaError:
            pass  # The managed item may already have been removed by the user.
    coordinator = OperationCoordinator()
    scanner = scanner or scan
    elevation = ElevationBridge([store.root])
    if product == 'bridge' and device_api is None:
        import device
        import jsymphonic
        device_api = SimpleNamespace(**{n: getattr(device, n) for n in ('find_walkman', 'device_info', 'backup_device', 'capture_device_identity')},
            **{n: getattr(jsymphonic, n) for n in ('list_tracks', 'add_tracks', 'remove_track', 'JSymphonicError',
                'list_playlists', 'create_playlist', 'update_playlist', 'delete_playlist')})
    service = MediaService(store, jobs, coordinator, scanner, elevation, device_api)
    cache = {}
    playback_lock = threading.RLock()
    playback_lease = None

    def release_playback(media_id):
        nonlocal playback_lease
        with playback_lock:
            if playback_lease and (media_id is None or playback_lease[0] == media_id):
                _, lease, ticket = playback_lease
                playback_lease = None
                lease.close()
                coordinator.finish(ticket)

    def acquire_playback(media_id):
        nonlocal playback_lease
        with playback_lock:
            if playback_lease and playback_lease[0] == media_id:
                return
            ticket = admit('playback_session')
            lease = ExitStack()
            try:
                lease.enter_context(store.lease(media_id))
                if playback_lease:
                    release_playback(playback_lease[0])
                playback_lease = (media_id, lease, ticket)
            except BaseException:
                lease.close()
                coordinator.finish(ticket)
                raise
    @asynccontextmanager
    async def lifespan(app):
        yield
        coordinator.begin_drain()
        await asyncio.to_thread(coordinator.wait_drained)
        jobs.close()
    app = FastAPI(title='Red Lotus Player' if product == 'player' else 'Walkman Bridge', version=VERSION, lifespan=lifespan)
    app.state.store, app.state.jobs, app.state.service = store, jobs, service
    app.state.coordinator, app.state.elevation = coordinator, elevation
    app.add_middleware(Boundary, token=token or os.environ.get('NIGHTOPS_TOKEN') or secrets.token_urlsafe(48),
        origin=origin or os.environ.get('NIGHTOPS_ORIGIN', 'http://127.0.0.1:8000'))
    for exception, status in ((MediaError, 400), (MediaBusyError, 409), (PlaylistConflictError, 409), (BridgeError, 409), (MultiPartException, 413)):
        async def error(request, exc, code=status):
            return JSONResponse({'detail': str(exc)}, status_code=code)
        app.add_exception_handler(exception, error)

    def admit(kind, identity=None, job_id=None):
        try:
            return coordinator.admit(kind, device=identity, job_id=job_id)
        except (DrainingError, BusyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    def identity():
        mount = device_api.find_walkman()
        if mount is None:
            raise HTTPException(404, 'No Walkman detected')
        return device_api.capture_device_identity(mount)

    @app.get('/api/health')
    async def health():
        mount = await asyncio.to_thread(device_api.find_walkman) if device_api else None
        return dict(ok=True, version=VERSION, product=product,
            java=bool(device_api and shutil.which(os.environ.get('WALKMAN_BRIDGE_JAVA', 'java'))),
            jar=bool(device_api and (Path(__file__).parent / 'vendor' / 'jsymphonic.jar').is_file()), device_connected=mount is not None)

    @app.get('/api/engine-busy')
    async def busy():
        return coordinator.snapshot()

    @app.post('/api/shutdown/drain')
    async def drain():
        return coordinator.begin_drain()

    @app.get('/api/scan/processes')
    async def scans():
        return scanner.snapshot()

    @app.get('/api/internal/scanner/pending')
    async def pending():
        return await asyncio.to_thread(elevation.pending)

    @app.post('/api/internal/scanner/claim')
    async def claim(request: Request):
        b = await request.json()
        return await asyncio.to_thread(elevation.claim, b['request_id'], b['nonce'], b['helper_pid'])

    @app.post('/api/internal/scanner/result')
    async def result(request: Request):
        return await asyncio.to_thread(elevation.submit, await request.json())

    @app.post('/api/internal/scanner/cancel')
    async def cancel(request: Request):
        b = await request.json()
        return await asyncio.to_thread(elevation.cancel, b['request_id'], b.get('reason_code', 'elevation_cancelled'))

    @app.get('/api/jobs/latest')
    async def latest():
        job = jobs.latest()
        return public_job(job) if job else None

    @app.get('/api/jobs/{job_id}')
    async def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, 'Unknown job')
        return public_job(job)

    @app.get('/api/queue')
    async def queue():
        return await asyncio.to_thread(lambda: dict(items=[store.public(r) for r in store.queue()],
            playlists=store.playlists(), quota=dict(used_bytes=store.used_bytes(), limit_bytes=store.limit)))

    @app.get('/api/playback-state')
    async def playback_state():
        return await asyncio.to_thread(store.playback_state)

    @app.patch('/api/playback-state')
    async def save_playback(body: PlaybackBody):
        # A final position update is allowed during drain after renderer pauses.
        return await asyncio.to_thread(store.playback_state, body.model_dump(exclude_unset=True))

    @app.post('/api/playback/lease')
    async def select_playback(body: PlaybackLeaseBody):
        await asyncio.to_thread(acquire_playback, body.media_id)
        return {'ok': True}

    @app.delete('/api/playback/lease')
    async def stop_playback(body: PlaybackLeaseBody):
        # Stop remains admitted during drain; stale releases cannot stop a newer track.
        await asyncio.to_thread(release_playback, body.media_id)
        return {'ok': True}

    @app.post('/api/internal/playback/release')
    async def renderer_stopped():
        # Native-only: Electron has acknowledged Stop or observed renderer death.
        await asyncio.to_thread(release_playback, None)
        return {'ok': True}

    @app.patch('/api/queue/order')
    async def reorder(body: OrderBody):
        ticket = admit('queue')
        try:
            await asyncio.to_thread(store.reorder, body.ids)
            return {'ok': True}
        finally:
            coordinator.finish(ticket)

    @app.delete('/api/queue/items/{media_id}')
    async def remove_media(media_id: str):
        ticket = admit('queue')
        try:
            await asyncio.to_thread(store.remove, media_id)
            return {'ok': True}
        finally:
            coordinator.finish(ticket)

    async def import_files(request, background, legacy_upload=False):
        volume = await asyncio.to_thread(identity) if legacy_upload else None
        job_id = uuid.uuid4().hex
        ticket = admit('import', volume, job_id)
        ids, form, leases = [], None, None
        try:
            if not request.headers.get('content-type', '').startswith('multipart/form-data'):
                raise HTTPException(415, 'Expected multipart audio files')
            # Admission and streaming limits precede multipart persistence.
            form = await LimitedMultipart(request.headers, request.stream(), max_files=MAX_FILES, max_fields=0).parse()
            files = form.getlist('files')
            if not files or any(not isinstance(f, UploadFile) for f in files) or len(files) != len(form.multi_items()):
                raise HTTPException(400, 'Provide audio files using the files field')
            for file in files:
                record = await asyncio.to_thread(store.import_file, file.filename, file.file)
                ids.append(record['id'])
            leases = service.hold(ids)
            kind = 'upload' if legacy_upload else 'import'
            service.create_job(job_id, ids, kind)
            background.add_task(service.run, job_id, ids, ticket, leases, kind)
            return {'job_id': job_id}
        except BaseException:
            if leases:
                leases.close()
            for media_id in ids:
                store.remove(media_id)
            coordinator.finish(ticket)
            raise
        finally:
            if form:
                await form.close()

    @app.post('/api/media/import')
    async def import_audio(request: Request, background_tasks: BackgroundTasks):
        return await import_files(request, background_tasks)

    @app.post('/api/media/import-link')
    async def import_link(body: LinkImportBody, background_tasks: BackgroundTasks):
        from link_import import validate_url, LinkImportError
        try:
            url = validate_url(body.url)
        except LinkImportError as exc:
            raise HTTPException(400, str(exc)) from exc
        job_id, media_id = uuid.uuid4().hex, uuid.uuid4().hex
        ticket = admit('import', job_id=job_id)
        try:
            job = jobs.create(job_id, 1, kind='link_import')
            job.set_files([dict(file_id=media_id, media_id=media_id, name='Link import',
                                state='queued', detail='Waiting to download', scan=None)])
            background_tasks.add_task(service.run_link_import, job_id, media_id, url, ticket)
            return {'job_id': job_id}
        except BaseException:
            coordinator.finish(ticket)
            raise

    @app.get('/api/media/{media_id}/artwork')
    async def artwork(media_id: str):
        ticket = admit('artwork_stream')
        def read_artwork():
            with store.lease(media_id) as record:
                if record.get('artwork_status') != 'ready' or 'artwork' not in record['artifacts']:
                    raise HTTPException(404, 'No cleared artwork for this track')
                path = store.path(record, 'artwork')
                with locked_read(path, store.root) as stream:
                    if not scanner.clearance_valid(path, record['artifacts']['artwork'].get('scan') or {}):
                        store.update(media_id, artwork_status='needs_scan')
                        raise HTTPException(423, 'Artwork needs Defender clearance')
                    data = stream.read(1024 * 1024 + 1)
                    if len(data) > 1024 * 1024:
                        raise HTTPException(413, 'Artwork exceeds the size limit')
                    return data
        try:
            return Response(await asyncio.to_thread(read_artwork), media_type='image/jpeg')
        finally:
            coordinator.finish(ticket)

    def schedule(ids, background, kind, volume=None):
        if len(ids) != len(set(ids)):
            raise HTTPException(400, 'Repeated media IDs are not allowed')
        job_id = uuid.uuid4().hex
        ticket = admit(kind, volume, job_id)
        leases = None
        try:
            leases = service.hold(ids)
            service.create_job(job_id, ids, kind)
            background.add_task(service.run, job_id, ids, ticket, leases, kind)
            return {'job_id': job_id}
        except BaseException:
            if leases:
                leases.close()
            coordinator.finish(ticket)
            raise

    @app.post('/api/media/{media_id}/rescan')
    async def rescan(media_id: str, background_tasks: BackgroundTasks):
        return schedule([media_id], background_tasks, 'rescan')

    @app.post('/api/media/{media_id}/prepare')
    async def prepare(media_id: str, body: PrepareBody, background_tasks: BackgroundTasks):
        if body.format != 'flac':
            raise HTTPException(400, 'Only lossless FLAC playback fallback is supported')
        return schedule([media_id], background_tasks, 'prepare')

    @app.api_route('/api/media/{media_id}/stream', methods=['GET', 'HEAD'])
    async def stream_audio(media_id: str, request: Request):
        ticket = admit('playback')
        stack = ExitStack()
        def release():
            try:
                stack.close()
            finally:
                coordinator.finish(ticket)
        def open_stream():
            record = stack.enter_context(store.lease(media_id))
            artifact = record['playback']
            path = store.path(record, artifact)
            stream = stack.enter_context(locked_read(path, store.root))
            clearance = record['artifacts'][artifact].get('scan')
            if not clearance or not scanner.clearance_valid(path, clearance):
                store.update(media_id, status='needs_scan')
                raise HTTPException(423, 'Defender clearance is required; rescan this item')
            size = path.stat().st_size
            start, end, status = parse_range(request.headers.get('range'), size)
            stream.seek(start)
            return stream, record, size, start, end, status
        try:
            source, record, size, start, end, status = await asyncio.to_thread(open_stream)
            headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(max(0, end - start + 1))}
            if status == 206:
                headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            if request.method == 'HEAD':
                response = Response(status_code=status, headers=headers, media_type=record['mime'])
            else:
                def chunks():
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
                return LeasedStream(chunks(), status_code=status, headers=headers, media_type=record['mime'], release=release)
        except BaseException:
            release()
            raise
        release()
        return response

    register_local_routes(app, store, admit, coordinator)
    if product == 'bridge':
        register_native_routes(app, device_api, identity, admit, coordinator, jobs, cache)
        register_device_routes(app, device_api, identity, admit, coordinator, jobs, data_dir, cache,
                               import_files, schedule, store, service)
    frontend = Path(__file__).parent.parent / 'frontend' / 'dist'
    if frontend.is_dir():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app

def register_device_routes(app, device_api, identity, admit, coordinator, jobs, data_dir, cache,
                           import_files, schedule, store, service):
    @app.post('/api/upload')
    async def upload(request: Request, background_tasks: BackgroundTasks):
        return await import_files(request, background_tasks, True)

    @app.post('/api/upload-folder')
    async def folder_disabled():
        raise HTTPException(410, 'Select files in the import dialog; arbitrary backend paths are no longer accepted')

    @app.post('/api/transfers')
    async def transfer(body: TransferBody, background_tasks: BackgroundTasks):
        for media_id in body.media_ids:
            record = store.get(media_id)
            if record.get('needs_reconcile'):
                raise HTTPException(409, 'Verify device state before retrying this uncertain transfer')
            if record['status'] != 'ready':
                raise HTTPException(409, 'Only ready media can be transferred')
        return schedule(body.media_ids, background_tasks, 'transfer', await asyncio.to_thread(identity))

    @app.get('/api/device')
    async def get_device():
        mount = await asyncio.to_thread(device_api.find_walkman)
        if mount is None:
            return {'connected': False}
        volume = await asyncio.to_thread(device_api.capture_device_identity, mount)
        ticket = admit('device_read', volume)
        def read():
            with coordinator.device_session(ticket) as verified:
                return {'connected': True, 'mount_path': str(verified), **device_api.device_info(verified)}
        try:
            return await asyncio.to_thread(read)
        finally:
            coordinator.finish(ticket)

    @app.get('/api/tracks')
    async def tracks(response: Response, refresh: int = 0, q: str | None = None):
        volume = await asyncio.to_thread(identity)
        ticket = admit('device_read', volume)
        def read():
            with coordinator.device_session(ticket) as mount:
                rows = cache.get(volume.volume_id)
                if rows is None or refresh:
                    rows = device_api.list_tracks(mount)
                    cache.clear()
                    cache[volume.volume_id] = rows
                return rows
        try:
            rows = await asyncio.to_thread(read)
            response.headers['ETag'] = track_etag(volume, rows)
            return rows if not q else [t for t in rows if any(q.casefold() in str(t.get(k) or '').casefold() for k in ('title', 'artist', 'album'))]
        except RuntimeError as exc:
            from jsymphonic import fatal_code_of
            code = fatal_code_of(exc)
            detail = {'message': str(exc), 'fatal_code': code} if code else str(exc)
            raise HTTPException(409, detail) from exc
        finally:
            coordinator.finish(ticket)

    @app.delete('/api/tracks/{track_id}')
    async def delete(track_id: str, request: Request):
        expected = request.headers.get('if-match')
        if not expected:
            raise HTTPException(428, 'Refresh the device list before deletion')
        volume = await asyncio.to_thread(identity)
        job_id = uuid.uuid4().hex
        ticket = admit('delete', volume, job_id)
        job = jobs.create(job_id, 1, kind='delete')
        job.set_files([dict(file_id=track_id, name=track_id, state='queued')])
        def remove():
            with coordinator.device_session(ticket) as mount:
                rows = device_api.list_tracks(mount)
                if track_etag(volume, rows) != expected:
                    raise HTTPException(409, 'Device list changed; refresh before deleting')
                if not any(str(t['id']) == track_id for t in rows):
                    raise HTTPException(404, 'Unknown device track')
                job.set_status(JobStatus.RUNNING, 'Deleting device track')
                job.set_phase('device_writing')
                job.update_file(track_id, state='transferring')
                device_api.remove_track(mount, track_id)
            job.update_file(track_id, state='transferred', detail='Deleted')
            job.set_phase('finished')
            job.set_status(JobStatus.DONE, 'Deleted device track')
        try:
            await asyncio.to_thread(remove)
            return {'ok': True, 'job_id': job_id}
        except HTTPException:
            job.update_file(track_id, state='failed', detail='Deletion rejected before device write')
            job.set_status(JobStatus.FAILED, 'Deletion rejected')
            raise
        except Exception as exc:
            from jsymphonic import fatal_code_of
            uncertain = job.phase == 'device_writing'
            fatal_code = fatal_code_of(exc)
            job.needs_reconcile = uncertain
            job.update_file(track_id, state='unknown' if uncertain else 'failed', detail=str(exc),
                            fatal_code=fatal_code)
            job.set_status(JobStatus.FAILED, 'Verify device state' if uncertain else str(exc))
            raise HTTPException(409, {'code': 'verify_device_state' if uncertain else 'delete_failed',
                                     'message': job.message, 'job_id': job_id,
                                     'fatal_code': fatal_code}) from exc
        finally:
            cache.clear()
            coordinator.finish(ticket)

    @app.post('/api/backup')
    async def backup():
        volume = await asyncio.to_thread(identity)
        ticket = admit('backup', volume)
        def copy():
            with coordinator.device_session(ticket) as mount:
                return device_api.backup_device(mount, data_dir / 'backups')
        try:
            dest = await asyncio.to_thread(copy)
            return {'ok': True, 'path': str(dest)}
        except Exception as exc:
            raise HTTPException(409, f'Backup failed: {exc}') from exc
        finally:
            coordinator.finish(ticket)
    original_write = service.write_device
    def write_and_invalidate(*args):
        try:
            return original_write(*args)
        finally:
            cache.clear()
    service.write_device = write_and_invalidate
