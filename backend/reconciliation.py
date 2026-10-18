"""Read-only device evidence and explicit acknowledgement of uncertain jobs.

Acknowledgement permits a later manual retry; it never asserts that an unknown
write succeeded. Original file outcomes and job status remain in the history.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from typing import Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field



class Acknowledgement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1, max_length=64)
    acknowledge: Literal[True]


def snapshot_etag(snapshot):
    raw = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    return '"' + hashlib.sha256(raw).hexdigest() + '"'


def register_reconciliation_routes(app, device_api, identity, admit, coordinator, jobs, store):
    def read_snapshot(mount, volume):
        journal = device_api.inspect_playlist_journal(mount)
        journal_clear = (isinstance(journal, dict) and journal.get('state') == 'none'
                         and journal.get('exists') is False and journal.get('covers') == {'files': []})
        # A pending journal intentionally prevents ordinary enumeration. Still
        # expose inspection so the owner can choose the separate recovery action.
        tracks, playlists, inventory_error = [], [], None
        if journal_clear:
            try:
                tracks = device_api.list_tracks(mount)
                playlists = device_api.list_playlists(mount)
            except RuntimeError as exc:
                inventory_error = {'message': str(exc), 'fatal_code': getattr(exc, 'code', None)}
        warnings = list(journal.get('warnings') or []) if isinstance(journal, dict) else []
        warnings += list(getattr(tracks, 'warnings', ()) or ())
        warnings += list(getattr(playlists, 'warnings', ()) or ())
        pending = jobs.pending_reconciliation()
        for job in pending:
            job['same_device'] = bool(job['files']) and all(
                row.get('volume_id') == volume.volume_id for row in job['files'])
        return {'volume_id': volume.volume_id, 'journal': journal, 'tracks': list(tracks),
                'playlists': list(playlists), 'warnings': warnings, 'jobs': pending,
                'inventory_error': inventory_error,
                'can_acknowledge': journal_clear and journal.get('guarded') is True
                                   and not warnings and inventory_error is None}

    async def inspect_or_acknowledge(response=None, request=None, body=None):
        expected = request.headers.get('if-match') if request else None
        if body is not None and not expected:
            raise HTTPException(428, 'Inspect the device before acknowledging an interrupted operation')
        volume = await asyncio.to_thread(identity)
        ticket = admit('device_reconcile', volume)

        def inspect():
            evidence = None
            def commit_verified():
                if evidence is not None:
                    jobs.acknowledge_device_state(body.job_id, evidence, store)
            with coordinator.device_session(ticket, on_verified=commit_verified) as mount:
                if coordinator.other_device_work(ticket):
                    raise HTTPException(409, 'Wait for admitted device work to finish before verification')
                snapshot = read_snapshot(mount, volume)
                etag = snapshot_etag(snapshot)
                if body is not None:
                    if etag != expected:
                        raise HTTPException(409, 'Device or operation changed; inspect again before confirming')
                    job = next((j for j in snapshot['jobs'] if j['job_id'] == body.job_id), None)
                    if not job or not job['same_device']:
                        raise HTTPException(409, 'This operation is not bound to the connected Walkman; keep it blocked')
                    if not snapshot['can_acknowledge']:
                        raise HTTPException(409, 'Resolve pending recovery and device warnings before verification')
                    evidence = {'outcome': 'owner_acknowledged', 'volume_id': volume.volume_id,
                                'snapshot_etag': etag, 'acknowledged_at': time.time(),
                                'track_count': len(snapshot['tracks']), 'playlist_count': len(snapshot['playlists'])}
                    return {'ok': True, 'job_id': body.job_id, 'automatic_retry': False}, None
                return snapshot, etag
        try:
            payload, etag = await asyncio.to_thread(inspect)
            if response is not None:
                response.headers['ETag'] = etag
            return payload
        except (RuntimeError, sqlite3.Error, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            coordinator.finish(ticket)

    @app.get('/api/device/reconciliation')
    async def inspect_device_reconciliation(response: Response):
        return await inspect_or_acknowledge(response=response)

    @app.post('/api/device/reconciliation')
    async def acknowledge_device_reconciliation(body: Acknowledgement, request: Request):
        return await inspect_or_acknowledge(request=request, body=body)
