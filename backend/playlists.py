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
            raise HTTPException(409, str(exc)) from exc
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
            uncertain = bool(job and job.phase == 'device_writing')
            if job:
                job.needs_reconcile = uncertain
                job.update_file(job_id, state='unknown' if uncertain else 'failed', detail=str(exc),
                    reason_code='device_outcome_unknown' if uncertain else 'playlist_failed')
                job.set_status(JobStatus.FAILED, 'Verify device state before another playlist edit' if uncertain else str(exc))
            raise HTTPException(409, {'code': 'verify_device_state' if uncertain else 'playlist_failed',
                'message': job.message if job else str(exc), 'job_id': job_id}) from exc
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
