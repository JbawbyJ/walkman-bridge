"""Local playlists and serialized Sony playlist routes.

Sony tables are read and written exclusively through the existing device engine.
ETags bind playlist edits to both the captured volume and its current track ledger.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from jobs import JobStatus
from media_store import playlist_name


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PlaylistCreateBody(StrictBody):
    name: str = Field(max_length=512)
    media_ids: list[str] = Field(default_factory=list, max_length=200)


class PlaylistPatchBody(StrictBody):
    name: str | None = Field(default=None, max_length=512)
    media_ids: list[str] | None = Field(default=None, max_length=200)


class MetadataBody(StrictBody):
    title: StrictStr | None = Field(default=None, max_length=512)
    artist: StrictStr | None = Field(default=None, max_length=512)
    album: StrictStr | None = Field(default=None, max_length=512)
    genre: StrictStr | None = Field(default=None, max_length=512)
    year: StrictStr | StrictInt | None = None
    track: StrictStr | None = Field(default=None, max_length=512)


class DevicePlaylistCreateBody(StrictBody):
    name: str = Field(max_length=512)
    track_ids: list[str] = Field(default_factory=list, max_length=200)


class DevicePlaylistPatchBody(StrictBody):
    name: str | None = Field(default=None, max_length=512)
    track_ids: list[str] | None = Field(default=None, max_length=200)


def shim_fatal_code(exc):
    """Lazy import: Player packages omit jsymphonic.py."""
    from jsymphonic import fatal_code_of
    return fatal_code_of(exc)


def shim_fatal_path(exc):
    from jsymphonic import fatal_path_of
    return fatal_path_of(exc)


def with_fatal_path(fields, exc):
    path = shim_fatal_path(exc)
    if path is None:
        return fields
    return {**fields, 'fatal_path': path}


def with_recovery_action(fields, code, *, recover=False):
    from jsymphonic import recovery_action_for
    return {**fields, 'recovery_action': recovery_action_for(code, recover=recover)}


def read_fatal_detail(exc):
    code = shim_fatal_code(exc)
    if not code:
        return str(exc)
    return with_fatal_path(with_recovery_action({'message': str(exc), 'fatal_code': code}, code), exc)


def expected_etag(request):
    value = request.headers.get('if-match')
    if not value:
        raise HTTPException(428, 'Refresh playlists before making changes')
    return value


def device_playlist_etag(volume, tracks, playlists):
    data = json.dumps([volume.volume_id, tracks, playlists], sort_keys=True, separators=(',', ':')).encode()
    return '"' + hashlib.sha256(data).hexdigest() + '"'


def register_local_routes(app, store, admit, coordinator):
    @app.get('/api/playlists')
    async def list_local_playlists():
        return {'items': await asyncio.to_thread(store.playlists)}

    @app.post('/api/playlists', status_code=201)
    async def create_local_playlist(body: PlaylistCreateBody, response: Response):
        ticket = admit('playlist')
        try:
            playlist = await asyncio.to_thread(store.create_playlist, body.name, body.media_ids)
            response.headers['ETag'] = playlist['etag']
            return playlist
        finally:
            coordinator.finish(ticket)

    @app.get('/api/playlists/{playlist_id}')
    async def get_local_playlist(playlist_id: str, response: Response):
        playlist = await asyncio.to_thread(store.get_playlist, playlist_id)
        response.headers['ETag'] = playlist['etag']
        return playlist

    @app.patch('/api/playlists/{playlist_id}')
    async def update_local_playlist(playlist_id: str, body: PlaylistPatchBody, request: Request, response: Response):
        expected = expected_etag(request)
        ticket = admit('playlist')
        try:
            playlist = await asyncio.to_thread(store.update_playlist, playlist_id,
                body.model_dump(exclude_unset=True), expected)
            response.headers['ETag'] = playlist['etag']
            return playlist
        finally:
            coordinator.finish(ticket)

    @app.delete('/api/playlists/{playlist_id}')
    async def delete_local_playlist(playlist_id: str, request: Request):
        expected = expected_etag(request)
        ticket = admit('playlist')
        try:
            await asyncio.to_thread(store.delete_playlist, playlist_id, expected)
            return {'ok': True}
        finally:
            coordinator.finish(ticket)

    @app.patch('/api/media/{media_id}/metadata')
    async def edit_media_metadata(media_id: str, body: MetadataBody):
        ticket = admit('metadata')
        try:
            record = await asyncio.to_thread(store.edit_metadata, media_id, body.model_dump(exclude_unset=True))
            return store.public(record)
        finally:
            coordinator.finish(ticket)


def register_native_routes(app, device_api, identity, admit, coordinator, jobs, cache):
    def read_snapshot(mount):
        tracks = device_api.list_tracks(mount)
        playlists = device_api.list_playlists(mount)
        return tracks, playlists

    @app.get('/api/device/playlists')
    async def native_playlists(response: Response):
        volume = await asyncio.to_thread(identity)
        ticket = admit('device_read', volume)
        def read():
            with coordinator.device_session(ticket) as mount:
                return read_snapshot(mount)
        try:
            tracks, playlists = await asyncio.to_thread(read)
            response.headers['ETag'] = device_playlist_etag(volume, tracks, playlists)
            return {'items': playlists}
        except RuntimeError as exc:
            raise HTTPException(409, read_fatal_detail(exc)) from exc
        finally:
            coordinator.finish(ticket)

    async def mutate(action, request, changes=None, playlist_id=None):
        expected = expected_etag(request)
        changes = changes or {}
        if action != 'delete' and not changes:
            raise HTTPException(400, 'Provide a playlist name or track IDs')
        if playlist_id is not None and not re.fullmatch(r'[1-9][0-9]{0,9}', playlist_id):
            raise HTTPException(400, 'Invalid device playlist ID')
        if 'name' in changes:
            changes['name'] = playlist_name(changes['name'])
            if len(changes['name'].encode('utf-16-le')) > 120:
                raise HTTPException(400, 'Sony playlist names must fit within 60 UTF-16 characters')
        if 'track_ids' in changes:
            ids = changes['track_ids']
            if not isinstance(ids, list) or len(ids) > 200 or any(not re.fullmatch(r'[1-9][0-9]{0,9}', key) for key in ids):
                raise HTTPException(400, 'Provide valid device track IDs')
        volume = await asyncio.to_thread(identity)
        job_id = uuid.uuid4().hex
        ticket = admit('playlist_' + action, volume, job_id)
        job = None
        def write():
            with coordinator.device_session(ticket) as mount:
                tracks, playlists = read_snapshot(mount)
                if device_playlist_etag(volume, tracks, playlists) != expected:
                    raise HTTPException(409, 'Device playlists or tracks changed; refresh before editing')
                if playlist_id is not None and not any(row['id'] == playlist_id for row in playlists):
                    raise HTTPException(404, 'Unknown device playlist')
                if action == 'create' and len(playlists) >= 200:
                    raise HTTPException(400, 'The device can contain at most 200 playlists')
                known = {str(row['id']) for row in tracks}
                if not set(changes.get('track_ids', [])).issubset(known):
                    raise HTTPException(400, 'A selected track is no longer on this Walkman')
                job.set_status(JobStatus.RUNNING, 'Updating Walkman playlist')
                # Persist write intent and row state together before the engine.
                job.set_files([dict(file_id=job_id, name=changes.get('name') or playlist_id or 'Playlist',
                    playlist_id=playlist_id, volume_id=volume.volume_id, state='transferring',
                    detail='Writing Walkman playlist')], phase='device_writing')
                if action == 'create':
                    result = device_api.create_playlist(mount, changes['name'], changes.get('track_ids', []))
                elif action == 'update':
                    result = device_api.update_playlist(mount, playlist_id, **changes)
                else:
                    device_api.delete_playlist(mount, playlist_id)
                    result = None
            job.update_file(job_id, state='transferred', detail='Playlist ' + ('deleted' if action == 'delete' else 'saved'),
                playlist_id=result['id'] if result else playlist_id)
            job.set_phase('finished')
            job.progress = 1
            job.set_status(JobStatus.DONE, 'Walkman playlist ' + ('deleted' if action == 'delete' else 'saved'))
            return result
        try:
            job = jobs.create(job_id, 1, kind='playlist_' + action)
            job.set_files([dict(file_id=job_id, name=changes.get('name') or playlist_id or 'Playlist',
                playlist_id=playlist_id, volume_id=volume.volume_id, state='queued')])
            result = await asyncio.to_thread(write)
            response = {'ok': True, 'job_id': job_id}
            if result is not None:
                response['playlist'] = result
            return response
        except HTTPException:
            if job:
                job.update_file(job_id, state='failed', detail='Playlist edit rejected before device write')
                job.set_phase('finished')
                job.set_status(JobStatus.FAILED, 'Playlist edit rejected')
            raise
        except Exception as exc:
            from jsymphonic import job_needs_reconcile
            writing = bool(job and job.phase == 'device_writing')
            uncertain = job_needs_reconcile(exc, writing)
            fatal_code = shim_fatal_code(exc)
            if job:
                job.needs_reconcile = uncertain
                job.update_file(job_id, **with_fatal_path(dict(
                    state='unknown' if uncertain else 'failed', detail=str(exc),
                    reason_code='device_outcome_unknown' if uncertain else 'playlist_failed',
                    fatal_code=fatal_code), exc))
                job.set_status(JobStatus.FAILED, 'Verify device state before another playlist edit' if uncertain else str(exc))
            raise HTTPException(409, with_fatal_path(with_recovery_action({
                'code': 'verify_device_state' if uncertain else 'playlist_failed',
                'message': job.message if job else str(exc), 'job_id': job_id,
                'fatal_code': fatal_code}, fatal_code), exc)) from exc
        finally:
            cache.clear()
            coordinator.finish(ticket)

    @app.post('/api/device/playlists', status_code=201)
    async def create_native_playlist(body: DevicePlaylistCreateBody, request: Request):
        return await mutate('create', request, body.model_dump())

    @app.patch('/api/device/playlists/{playlist_id}')
    async def update_native_playlist(playlist_id: str, body: DevicePlaylistPatchBody, request: Request):
        return await mutate('update', request, body.model_dump(exclude_unset=True), playlist_id)

    @app.delete('/api/device/playlists/{playlist_id}')
    async def delete_native_playlist(playlist_id: str, request: Request):
        return await mutate('delete', request, playlist_id=playlist_id)

    @app.get('/api/device/playlist-recovery/inspect')
    async def inspect_playlist_journal():
        volume = await asyncio.to_thread(identity)
        ticket = admit('device_read', volume)
        def read():
            with coordinator.device_session(ticket) as mount:
                return device_api.inspect_playlist_journal(mount)
        try:
            return await asyncio.to_thread(read)
        except RuntimeError as exc:
            raise HTTPException(409, read_fatal_detail(exc)) from exc
        finally:
            coordinator.finish(ticket)

    async def recover_or_repair(action):
        volume = await asyncio.to_thread(identity)
        job_id = uuid.uuid4().hex
        ticket = admit('playlist_' + action, volume, job_id)
        job = None

        def library_not_loaded():
            return with_recovery_action({
                'code': 'library_not_loaded',
                'message': 'Playlist repair refused: the Walkman library is not loaded',
                'job_id': job_id,
                'fatal_code': 'PLAYLIST_LIBRARY_NOT_LOADED',
            }, 'PLAYLIST_LIBRARY_NOT_LOADED')

        def write():
            with coordinator.device_session(ticket) as mount:
                if action == 'repair':
                    # Always list. A stale non-empty cache must not skip the empty-library refusal.
                    rows = device_api.list_tracks(mount)
                    if not rows:
                        raise HTTPException(409, library_not_loaded())
                job.set_status(JobStatus.RUNNING, 'Updating Walkman playlists')
                job.set_files([dict(file_id=job_id, name='Playlist ' + action, volume_id=volume.volume_id,
                    state='transferring', detail='Writing Walkman playlist recovery')], phase='device_writing')
                if action == 'recover':
                    result = device_api.recover_playlist_journal(mount)
                else:
                    result = device_api.repair_playlists(mount)
            job.update_file(job_id, state='transferred', detail='Playlist ' + action + ' finished')
            job.set_phase('finished')
            job.progress = 1
            job.set_status(JobStatus.DONE, 'Playlist ' + action + ' finished')
            return result
        try:
            job = jobs.create(job_id, 1, kind='playlist_' + action)
            job.set_files([dict(file_id=job_id, name='Playlist ' + action, volume_id=volume.volume_id, state='queued')])
            result = await asyncio.to_thread(write)
            body = {'ok': True, 'job_id': job_id}
            if action == 'recover':
                body['outcome'] = result['outcome']
            else:
                body['pruned_count'] = result['pruned_count']
                body['pruned_track_ids'] = result['pruned_track_ids']
                body['playlist_ids'] = result['playlist_ids']
            return body
        except HTTPException as exc:
            if job:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                job.needs_reconcile = False
                job.update_file(job_id, state='failed',
                    detail=detail.get('message') or 'Playlist recovery rejected before device write',
                    reason_code=detail.get('code') or 'playlist_failed',
                    fatal_code=detail.get('fatal_code'))
                job.set_phase('finished')
                job.set_status(JobStatus.FAILED, detail.get('message') or 'Playlist recovery rejected')
            raise
        except Exception as exc:
            from jsymphonic import job_needs_reconcile
            writing = bool(job and job.phase == 'device_writing')
            recover = action == 'recover'
            uncertain = job_needs_reconcile(exc, writing, recover=recover)
            fatal_code = shim_fatal_code(exc)
            if fatal_code == 'PLAYLIST_LIBRARY_NOT_LOADED':
                detail_code = 'library_not_loaded'
                file_state = 'failed'
                reason = 'library_not_loaded'
                status = str(exc)
            elif uncertain:
                detail_code = 'verify_device_state'
                file_state = 'unknown'
                reason = 'device_outcome_unknown'
                status = 'Verify device state before another playlist recovery'
            else:
                detail_code = 'playlist_failed'
                file_state = 'failed'
                reason = 'playlist_failed'
                status = str(exc)
            if job:
                job.needs_reconcile = uncertain
                job.update_file(job_id, **with_fatal_path(dict(
                    state=file_state, detail=str(exc), reason_code=reason, fatal_code=fatal_code), exc))
                job.set_status(JobStatus.FAILED, status)
            raise HTTPException(409, with_fatal_path(with_recovery_action({
                'code': detail_code,
                'message': job.message if job else str(exc), 'job_id': job_id,
                'fatal_code': fatal_code}, fatal_code, recover=recover), exc)) from exc
        finally:
            cache.clear()
            coordinator.finish(ticket)

    @app.post('/api/device/playlist-recovery/recover')
    async def recover_playlist_journal():
        return await recover_or_repair('recover')

    @app.post('/api/device/playlist-recovery/repair')
    async def repair_playlists():
        return await recover_or_repair('repair')
