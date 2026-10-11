"""Scan-before-consume orchestration. Jobs retain input identity through every phase."""
from __future__ import annotations
import hashlib
from contextlib import ExitStack
from pathlib import Path
from jobs import JobStatus
from media_store import MAX_FILE, MediaError, locked_read, no_links
from transcode import convert_managed, analyze_cleared_audio, extract_cleared_artwork
from metadata import normalize_metadata, TRANSFER_TAGS
from scan_bridge import ELEVATION_WAIT_SECONDS

METADATA_POLICY = 2


class MediaService:
    def __init__(self, store, jobs, coordinator, scanner, bridge, device_api=None):
        self.store, self.jobs, self.coordinator = store, jobs, coordinator
        self.scanner, self.bridge, self.device_api = scanner, bridge, device_api

    def hold(self, ids):
        stack = ExitStack()
        try:
            stack.enter_context(self.store.work(ids))
            for media_id in ids:
                stack.enter_context(self.store.lease(media_id))
            return stack
        except BaseException:
            stack.close()
            raise

    def create_job(self, job_id, ids, kind):
        job = self.jobs.create(job_id, len(ids), kind=kind)
        job.set_files([dict(file_id=media_id, media_id=media_id,
                            name=self.store.get(media_id)['name'], state='queued',
                            detail='', reason_code=None, scan=None) for media_id in ids])
        return job

    def clear(self, media_id, artifact='source', job=None, batch_id=None):
        record = self.store.get(media_id)
        path = self.store.path(record, artifact)
        previous = record['artifacts'][artifact].get('scan')
        if previous and self.scanner.clearance_valid(path, previous):
            if artifact == 'artwork':
                self.store.update(media_id, artwork_status='ready')
                return previous
            self.store.update(media_id, status='ready', scan=previous)
            if job:
                job.update_file(media_id, state='ready', scan=previous, detail='Cleared')
            return previous
        if artifact == 'artwork':
            self.store.update(media_id, artwork_status='scanning')
        else:
            self.store.update(media_id, status='scanning')
        if job:
            job.set_phase('scanning')
            job.update_file(media_id, state='scanning')
        scan_file = self.scanner.scan_artwork if artifact == 'artwork' else self.scanner.scan_audio
        result = scan_file(path).to_dict()
        trusted_status = None
        if result.get('reason_code') == 'defender_elevation_required':
            request = self.bridge.request(media_id, path, result['sha256'], result['size_bytes'], batch_id=batch_id)
            if artifact == 'artwork':
                self.store.update(media_id, artwork_status='awaiting_permission')
            else:
                self.store.update(media_id, status='awaiting_permission', scan=result)
            if job:
                job.update_file(media_id, state='awaiting_permission', scan=result,
                                detail='Awaiting Defender scan permission')
            if batch_id:
                return {'pending_request': request}
            elevated = self.bridge.wait(request['request_id'], timeout=ELEVATION_WAIT_SECONDS)
            result = elevated or self.bridge.cancel(request['request_id'], 'elevation_timeout')
            if result.get('ok') and not self.scanner.clearance_valid(path, result):
                trusted_status = self.bridge.trusted_status(result)
        return self.accept_scan(media_id, artifact, result, job, trusted_status)

    def accept_scan(self, media_id, artifact, result, job=None, trusted_status=None):
        path = self.store.path(self.store.get(media_id), artifact)
        # This also verifies elevated signature status against live Defender.
        valid = bool(result.get('ok')) and (self.scanner.clearance_valid(path, result,
            trusted_status=trusted_status) if trusted_status else self.scanner.clearance_valid(path, result))
        if not valid and result.get('ok'):
            result = dict(result, ok=False, state='ERROR', reason_code='clearance_unverifiable',
                          reason='Defender clearance could not be verified', defender_status='error')
        record = self.store.get(media_id)
        record['artifacts'][artifact]['scan'] = result
        status = 'ready' if valid else ('blocked' if result.get('state') in ('THREAT', 'BLOCKED', 'REJECTED') else 'failed')
        if artifact == 'artwork':
            self.store.update(media_id, artifacts=record['artifacts'], artwork_status=status)
        else:
            self.store.update(media_id, artifacts=record['artifacts'], status=status, scan=result)
        if job:
            job.update_file(media_id, state=status, scan=result,
                            reason_code=result.get('reason_code'), detail=result.get('reason', ''))
        if not valid:
            raise MediaError(result.get('reason') or 'Explicit Defender clearance is required')
        return result

    def clear_group(self, ids, artifact, job):
        """Publish one complete pending batch, not one UAC request per file."""
        batch_id = job.job_id + ':' + artifact
        ready, pending = [], []
        try:
            with ExitStack() as locks:
                for media_id in ids:
                    try:
                        record = self.store.get(media_id)
                        locks.enter_context(locked_read(self.store.path(record, artifact), self.store.root))
                        result = self.clear(media_id, artifact, job, batch_id=batch_id)
                        if 'pending_request' in result:
                            pending.append((media_id, result['pending_request']))
                        else:
                            ready.append(media_id)
                    except Exception as exc:
                        self.scan_failure(job, media_id, exc)
                self.bridge.release_batch(batch_id)
                for media_id, request in pending:
                    try:
                        result = self.bridge.wait(request['request_id'], timeout=ELEVATION_WAIT_SECONDS)
                        result = result or self.bridge.cancel(request['request_id'], 'elevation_timeout')
                        path = self.store.path(self.store.get(media_id), artifact)
                        trusted = (self.bridge.trusted_status(result) if result.get('ok')
                                   and not self.scanner.clearance_valid(path, result) else None)
                        self.accept_scan(media_id, artifact, result, job, trusted)
                        ready.append(media_id)
                    except Exception as exc:
                        self.scan_failure(job, media_id, exc)
            return [media_id for media_id in ids if media_id in ready]
        finally:
            self.bridge.cancel_batch(batch_id)

    def scan_failure(self, job, media_id, exc):
        row = next(row for row in job.to_dict()['files'] if row['file_id'] == media_id)
        if row['state'] not in ('blocked', 'failed'):
            job.update_file(media_id, state='failed', detail=str(exc), reason_code='scan_failed')
            self.store.update(media_id, status='failed')

    def derivative(self, media_id, kind, job, scan_output=True):
        record = self.store.get(media_id)
        source = self.store.path(record)
        with locked_read(source, self.store.root):
            self.clear(media_id, job=job)
            self.prepare_metadata(media_id)
            record = self.store.get(media_id)
            existing = record['artifacts'].get(kind)
            if existing and kind == 'transfer' and (existing.get('metadata_policy') != METADATA_POLICY
                    or existing.get('metadata_revision', 0) != record.get('metadata_revision', 0)):
                # A previous release's untagged transfer.mp3 must not retain cache labels.
                self.store.path(record, kind).unlink()
                record['artifacts'].pop(kind)
                self.store.update(media_id, artifacts=record['artifacts'])
                existing = None
            if existing:
                path = self.store.path(record, kind)
                if scan_output:
                    with locked_read(path, self.store.root):
                        self.clear(media_id, kind, job)
                return path
            job.set_phase('converting')
            job.update_file(media_id, state='transcoding', detail='Preparing lossless playback' if kind == 'playback' else 'Preparing Walkman MP3')
            out = source.parent / ('playback.flac' if kind == 'playback' else 'transfer.mp3')
            with self.store.reserve(MAX_FILE):
                try:
                    convert_managed(source, out, kind, MAX_FILE,
                                    metadata=normalize_metadata(record, record['name']))
                    with locked_read(out, self.store.root) as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                        record = self.store.get(media_id)
                        record['artifacts'][kind] = dict(name=out.name, size_bytes=out.stat().st_size,
                                                        sha256=digest, scan=None, metadata_policy=METADATA_POLICY,
                                                        metadata_revision=record.get('metadata_revision', 0))
                        self.store.update(media_id, artifacts=record['artifacts'])
                        if scan_output:
                            self.clear(media_id, kind, job)
                except BaseException:
                    # Failed generated bytes have no consumer and cannot remain playable.
                    out.unlink(missing_ok=True)
                    record = self.store.get(media_id)
                    record['artifacts'].pop(kind, None)
                    self.store.update(media_id, artifacts=record['artifacts'])
                    raise
            return out

    def prepare_metadata(self, media_id):
        """Caller holds a cleared source read lock; never trust display fallback as tags."""
        record = self.store.get(media_id)
        if record.get('metadata_policy') == METADATA_POLICY:
            return record
        measured = analyze_cleared_audio(self.store.path(record))
        hints = record.get('metadata_hints') or {}
        embedded = {key: value for key, value in measured.items()
                    if value is not None and (not isinstance(value, str) or value.strip())}
        tags = normalize_metadata({**hints, **embedded, **(record.get('metadata_overrides') or {})}, record['name'])
        # Clear previously stored malformed optional tags instead of restoring raw decoder text.
        values = {key: tags.get(key) for key in TRANSFER_TAGS}
        values.update({key: measured[key] for key in ('duration_seconds', 'integrated_lufs', 'has_artwork')
                       if key in measured})
        return self.store.update(media_id, **values, analyzed=True, metadata_policy=METADATA_POLICY)

    def prepare_artwork(self, media_id):
        record = self.store.get(media_id)
        if not record.get('has_artwork'):
            return
        out = self.store.path(record).parent / 'artwork.jpg'
        with locked_read(self.store.path(record), self.store.root):
            # Source clearance failure fails the import even when artwork is optional.
            self.clear(media_id)
            try:
                if 'artwork' not in record['artifacts']:
                    with self.store.reserve(1024 * 1024):
                        generated = extract_cleared_artwork(self.store.path(record), out)
                        if generated is None:
                            self.store.update(media_id, artwork_status='none')
                            return
                        with locked_read(out, self.store.root) as image:
                            digest = hashlib.file_digest(image, 'sha256').hexdigest()
                            record = self.store.get(media_id)
                            record['artifacts']['artwork'] = dict(name=out.name,
                                size_bytes=out.stat().st_size, sha256=digest, scan=None)
                            self.store.update(media_id, artifacts=record['artifacts'])
                with locked_read(out, self.store.root):
                    self.clear(media_id, 'artwork')
            except Exception:
                # Artwork is optional, but unchecked bytes are never sent to the renderer.
                self.store.update(media_id, artwork_status='failed')

    def run_link_import(self, job_id, media_id, url, ticket):
        import link_import
        job = self.jobs.get(job_id)
        delegated, leases = False, None
        try:
            job.set_status(JobStatus.RUNNING, 'Downloading audio')
            job.set_phase('downloading')
            job.update_file(media_id, state='downloading', detail='Downloading audio')
            def progress(snapshot):
                # Persist bounded progress independently from any downloader log text.
                downloaded, total = snapshot.get('downloaded_bytes', 0), snapshot.get('total_bytes')
                job.update_file(media_id, downloaded_bytes=max(0, int(downloaded)),
                                total_bytes=int(total) if total else None)
            with self.store.download_workspace(job_id) as folder:
                downloaded = link_import.download_link(url, folder, MAX_FILE, progress=progress)
                path = Path(downloaded.path)
                if not path.absolute().is_relative_to(folder.absolute()):
                    raise MediaError('Download escaped its managed directory')
                no_links(path, self.store.root)
                with locked_read(path, self.store.root) as stream:
                    record = self.store.import_file(downloaded.name, stream, media_id=media_id)
                leases = self.hold([media_id])
                # Provider metadata is text, never instructions/HTML or a filesystem path.
                hints = normalize_metadata(downloaded.metadata, record['name'])
                self.store.update(media_id, metadata_hints=hints, import_source='link')
                job.update_file(media_id, name=record['name'], state='queued', detail='Downloaded; awaiting Defender clearance')
            delegated = True
            self.run(job_id, [media_id], ticket, leases, kind='import')
        except Exception as exc:
            # Do not expose download URLs, tokens or provider diagnostics from an unknown exception.
            detail = str(exc) if isinstance(exc, (MediaError, link_import.LinkImportError)) else 'Link import failed before playback; retry the link'
            job.update_file(media_id, state='failed', detail=detail, reason_code='link_import_failed')
            job.set_status(JobStatus.FAILED, detail)
            job.set_phase('finished')
        finally:
            if not delegated:
                if leases:
                    leases.close()
                self.coordinator.finish(ticket)

    def run(self, job_id, ids, ticket, leases, kind='import'):
        job = self.jobs.get(job_id)
        job.set_status(JobStatus.RUNNING, 'Processing managed audio')
        try:
            ready = []
            cleared = self.clear_group(ids, 'source', job)
            for index, media_id in enumerate(cleared):
                try:
                    if kind in ('transfer', 'upload', 'prepare'):
                        path = self.derivative(media_id, 'playback' if kind == 'prepare' else 'transfer', job, scan_output=False)
                        ready.append((media_id, path))
                    else:
                        record = self.store.get(media_id)
                        with locked_read(self.store.path(record), self.store.root):
                            self.clear(media_id, job=job)
                            if kind in ('import', 'rescan'):
                                job.set_phase('analyzing')
                                self.prepare_metadata(media_id)
                        self.prepare_artwork(media_id)
                        ready.append((media_id, self.store.path(record)))
                except Exception as exc:
                    row = next(x for x in job.to_dict()['files'] if x['file_id'] == media_id)
                    if row['state'] not in ('failed', 'blocked'):
                        job.update_file(media_id, state='failed', detail=str(exc), reason_code='processing_failed')
                        if kind == 'import':
                            self.store.update(media_id, status='failed')
                job.progress = (index + 1) / max(1, len(ids)) * (0.5 if kind in ('upload', 'transfer') else 1)
                job.touch()
            if kind in ('upload', 'transfer', 'prepare') and ready:
                output_kind = 'playback' if kind == 'prepare' else 'transfer'
                output_ready = self.clear_group([media_id for media_id, _ in ready], output_kind, job)
                ready = [(media_id, path) for media_id, path in ready if media_id in output_ready]
                if kind == 'prepare':
                    for media_id, _ in ready:
                        self.store.update(media_id, playback='playback', mime='audio/flac')
            if kind == 'rescan':
                playback_ids = [media_id for media_id, _ in ready
                                if self.store.get(media_id).get('playback') == 'playback']
                if playback_ids:
                    self.clear_group(playback_ids, 'playback', job)
            if kind in ('upload', 'transfer') and ready:
                self.write_device(job, ready, ticket)
            rows = job.to_dict()['files']
            good = sum(row['state'] in ('ready', 'transferred') for row in rows)
            unknown = any(row['state'] == 'unknown' for row in rows)
            status = JobStatus.DONE if good == len(ids) else JobStatus.PARTIAL if good else JobStatus.FAILED
            message = 'Verify device state before retrying' if unknown else f'Completed {good} of {len(ids)} files'
            job.progress = 1
            job.set_phase('finished')
            job.set_status(status, message)
        except Exception as exc:
            # Preserve any device-writing uncertainty already persisted by write_device.
            for row in job.to_dict()['files']:
                if row['state'] not in ('ready', 'transferred', 'blocked', 'failed', 'unknown'):
                    job.update_file(row['file_id'], state='failed', detail=str(exc), reason_code='job_failed')
            job.set_status(JobStatus.FAILED, 'Verify device state' if job.needs_reconcile else str(exc))
        finally:
            leases.close()
            self.coordinator.finish(ticket)

    def write_device(self, job, ready, ticket):
        if not self.device_api:
            raise MediaError('This product has no device engine')
        writing = False
        try:
            with self.coordinator.device_session(ticket) as mount, ExitStack() as stack:
                for media_id, path in ready:
                    if self.store.get(media_id).get('needs_reconcile'):
                        raise MediaError('Verify device state before another transfer of this track')
                    stack.enter_context(locked_read(path, self.store.root))
                    self.clear(media_id, 'transfer', job)
                job.set_phase('device_writing')
                for media_id, _ in ready:
                    job.update_file(media_id, state='transferring', detail='Writing to Walkman')
                writing = True
                result = self.device_api.add_tracks(mount, [p for _, p in ready],
                    lambda event: job.log(str(event.get('message') or event.get('event') or 'Device progress')))
            # Publish only after the coordinator's post-write identity check succeeded.
            job.needs_reconcile = bool(result.needs_reconcile)
            if len(result.files) != len(ready):
                raise MediaError('Incomplete device result')
            for outcome in result.files:
                media_id, expected = ready[outcome.input_index]
                if outcome.path != str(expected):
                    raise MediaError('Device result did not match the admitted file')
                job.update_file(media_id, state=outcome.state, detail=outcome.detail, reason_code=outcome.reason_code)
                if result.needs_reconcile or outcome.state == 'unknown':
                    self.store.update(media_id, needs_reconcile=True)
        except Exception as exc:
            from jsymphonic import fatal_code_of, fatal_path_of, job_needs_reconcile
            uncertain = job_needs_reconcile(exc, writing)
            fatal_code = fatal_code_of(exc)
            path = fatal_path_of(exc)
            job.needs_reconcile = uncertain
            for media_id, _ in ready:
                changes = dict(state='unknown' if uncertain else 'failed', detail=str(exc),
                               reason_code='verify_device_state' if uncertain else 'device_admission_failed',
                               fatal_code=fatal_code)
                if path is not None:
                    changes['fatal_path'] = path
                job.update_file(media_id, **changes)
                if uncertain:
                    self.store.update(media_id, needs_reconcile=True)
            job.touch()
