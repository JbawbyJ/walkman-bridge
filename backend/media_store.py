"""Private content-addressed evidence and queue, with managed-artifact leases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import uuid
import unicodedata
from contextlib import contextmanager
from pathlib import Path

MAX_FILE = 500 * 1024**2
MAX_BATCH = 2 * 1024**3
MAX_FILES = 200
CACHE_LIMIT = 10 * 1024**3
MIMES = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.flac': 'audio/flac',
         '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.ogg': 'audio/ogg'}

def public_scan(record):
    if not isinstance(record, dict):
        return None
    return {key: record.get(key) for key in ('ok', 'state', 'reason', 'reason_code', 'defender_status')}

def public_job(job):
    result = job.to_dict()
    for row in result.get('files', []):
        row['scan'] = public_scan(row.get('scan'))
    return result


class MediaError(ValueError):
    pass


class MediaBusyError(MediaError):
    pass


class PlaylistConflictError(MediaError):
    pass


def playlist_name(value):
    if not isinstance(value, str) or len(value) > 512:
        raise MediaError('Playlist name must be text of at most 120 characters')
    value = ' '.join(''.join(char for char in value
        if not unicodedata.category(char).startswith('C') or char.isspace()).split())
    if not value or len(value) > 120:
        raise MediaError('Playlist name must contain 1 to 120 characters')
    return value


def no_links(path: Path, root: Path):
    """Reject Windows junctions and symlinks at every existing component."""
    path = path.absolute()
    root = root.absolute()
    if not path.is_relative_to(root):
        raise MediaError('Managed path escaped the cache')
    for component in [path, *path.parents]:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise MediaError('Reparse points are not allowed in managed storage')


@contextmanager
def locked_read(path: Path, root: Path):
    """Keep scanned bytes immutable through consumption on Windows."""
    no_links(path, root)
    if os.name == 'nt':
        import ctypes
        import msvcrt
        from ctypes import wintypes
        create = ctypes.WinDLL('kernel32', use_last_error=True).CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        # Share READ only: neither writers nor delete/rename can race clearance.
        handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), 'Cannot lock managed audio')
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))
            raise
        stream = os.fdopen(fd, 'rb')
    else:
        stream = path.open('rb')
    try:
        no_links(path, root)
        yield stream
    finally:
        stream.close()


class MediaStore:
    def __init__(self, db_path: Path, root: Path, limit=CACHE_LIMIT):
        self.root = root.absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        no_links(self.root, self.root)
        self.limit = limit
        self._lock = threading.RLock()
        self._leases = {}
        self._working = set()
        self._reserved = 0
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute('''CREATE TABLE IF NOT EXISTS media (
            id TEXT PRIMARY KEY, record TEXT NOT NULL, position INTEGER)''')
        self._db.execute('CREATE TABLE IF NOT EXISTS playback_state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        self._db.execute('''CREATE TABLE IF NOT EXISTS local_playlists (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, media_ids TEXT NOT NULL,
            revision INTEGER NOT NULL, position INTEGER NOT NULL)''')
        self._db.commit()
        for row in self._db.execute('SELECT id,record,position FROM media').fetchall():
            if row['position'] is None:
                self._cleanup(row['id'])
                continue
            record = json.loads(row['record'])
            if record['status'] in ('queued', 'scanning', 'awaiting_permission'):
                self.update(row['id'], status='needs_scan')
        self._recover_orphans()

    def _recover_orphans(self):
        """Recover incomplete managed copies without evicting any queued artifact."""
        known = {row['id']: json.loads(row['record']) for row in
                 self._db.execute('SELECT id,record FROM media')}
        for folder in self.root.iterdir():
            no_links(folder, self.root)
            if folder.is_dir() and re.fullmatch(r'download-[a-f0-9]{32}', folder.name):
                for child in folder.rglob('*'):
                    no_links(child, self.root)
                shutil.rmtree(folder)
                continue
            if not folder.is_dir() or not re.fullmatch(r'[a-f0-9]{32}', folder.name):
                continue
            if folder.name not in known:
                for child in folder.rglob('*'):
                    no_links(child, self.root)
                shutil.rmtree(folder)
                continue
            retained = {value['name'] for value in known[folder.name]['artifacts'].values()}
            for name in ('playback.flac', 'transfer.mp3', 'artwork.jpg'):
                path = folder / name
                if name not in retained and path.exists():
                    no_links(path, self.root)
                    path.unlink()

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
            raise MediaError('Unknown media ID')
        return value

    def get(self, media_id):
        self._id(media_id)
        with self._lock:
            row = self._db.execute('SELECT record FROM media WHERE id=?', (media_id,)).fetchone()
            if row is None:
                raise MediaError('Unknown media ID')
            return json.loads(row['record'])

    def path(self, record, artifact='source'):
        media_id = self._id(record['id'])
        name = record['artifacts'][artifact]['name']
        if Path(name).name != name or ':' in name or '\\' in name:
            raise MediaError('Invalid artifact name')
        path = self.root / media_id / name
        no_links(path, self.root)
        return path

    def queue(self):
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute(
                'SELECT record FROM media WHERE position IS NOT NULL ORDER BY position')]

    def used_bytes(self):
        with self._lock:
            # Account for interrupted copies and derivatives not yet committed to SQLite.
            total = 0
            for path in self.root.rglob('*'):
                no_links(path, self.root)
                if path.is_file():
                    total += path.stat().st_size
            return total

    @contextmanager
    def work(self, ids):
        """Atomically admit one processing job per media ID, independent of streams."""
        with self._lock:
            if self._working.intersection(ids):
                raise MediaBusyError('A selected track is already being processed')
            self._working.update(ids)
        try:
            yield
        finally:
            with self._lock:
                self._working.difference_update(ids)

    @contextmanager
    def reserve(self, size):
        with self._lock:
            if size < 0 or self.used_bytes() + self._reserved + size > self.limit:
                raise MediaError('Managed cache is full; remove queue items to free space')
            self._reserved += size
        try:
            yield
        finally:
            with self._lock:
                self._reserved -= size

    @contextmanager
    def download_workspace(self, job_id):
        folder = self.root / ('download-' + self._id(job_id))
        with self.reserve(MAX_FILE):
            folder.mkdir()
            try:
                no_links(folder, self.root)
                yield folder
            finally:
                no_links(folder, self.root)
                for child in folder.rglob('*'):
                    no_links(child, self.root)
                shutil.rmtree(folder)

    def import_file(self, name, stream, *, media_id=None):
        name = (name or 'Untitled').replace('\\', '/').split('/')[-1][:240]
        suffix = Path(name).suffix.lower()
        # Unknown types are persisted for a structured format rejection, never decoded.
        extension = suffix if suffix in MIMES else '.bin'
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        if size > MAX_FILE:
            raise MediaError('File exceeds 500 MiB')
        media_id = self._id(media_id) if media_id is not None else uuid.uuid4().hex
        folder = self.root / media_id
        with self.reserve(size):
            folder.mkdir()
            try:
                no_links(folder, self.root)
                digest = hashlib.sha256()
                path = folder / ('source' + extension)
                count = 0
                with path.open('xb') as target:
                    while chunk := stream.read(1024 * 1024):
                        count += len(chunk)
                        if count > size:
                            raise MediaError('Import changed size during copy')
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if count != size:
                    raise MediaError('Import ended before the expected size')
                record = dict(id=media_id, name=name, title=Path(name).stem,
                    artist='', album='', duration_seconds=None,
                    mime=MIMES.get(suffix, 'application/octet-stream'), size_bytes=size,
                    status='queued', scan=None, stream_url=f'/api/media/{media_id}/stream',
                    artifacts={'source': dict(name=path.name, size_bytes=size,
                                             sha256=digest.hexdigest(), scan=None)}, playback='source')
                with self._lock:
                    position = self._db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM media').fetchone()[0]
                    self._db.execute('INSERT INTO media VALUES(?,?,?)',
                                     (media_id, json.dumps(record), position))
                    self._db.commit()
                return record
            except BaseException:
                shutil.rmtree(folder)
                raise

    def update(self, media_id, **changes):
        with self._lock:
            record = self.get(media_id)
            record.update(changes)
            self._db.execute('UPDATE media SET record=? WHERE id=?', (json.dumps(record), media_id))
            self._db.commit()
            return record

    def reorder(self, ids):
        with self._lock:
            current = [r['id'] for r in self.queue()]
            if len(ids) != len(set(ids)) or set(ids) != set(current):
                raise MediaError('Queue order must be an exact permutation of the current queue')
            self._db.executemany('UPDATE media SET position=? WHERE id=?', enumerate(ids))
            self._db.commit()

    def remove(self, media_id):
        with self._lock:
            self.get(media_id)
            # Membership and library removal commit together. Existing consumers
            # keep their lease until they finish; saved playlists retain no ghosts.
            with self._db:
                self._db.execute('UPDATE media SET position=NULL WHERE id=?', (media_id,))
                for row in self._db.execute('SELECT id,media_ids FROM local_playlists').fetchall():
                    ids = json.loads(row['media_ids'])
                    if media_id in ids:
                        self._db.execute('UPDATE local_playlists SET media_ids=?,revision=revision+1 WHERE id=?',
                            (json.dumps([key for key in ids if key != media_id]), row['id']))
            self._cleanup(media_id)

    def _cleanup(self, media_id):
        with self._lock:
            row = self._db.execute('SELECT position FROM media WHERE id=?', (media_id,)).fetchone()
            if row is None or row[0] is not None or self._leases.get(media_id, 0):
                return
            folder = self.root / self._id(media_id)
            if folder.exists():
                no_links(folder, self.root)
                # Only this store creates artifact names. Reject unexpected links before recursive removal.
                for child in folder.rglob('*'):
                    no_links(child, self.root)
                shutil.rmtree(folder)
            self._db.execute('DELETE FROM media WHERE id=?', (media_id,))
            self._db.commit()

    @contextmanager
    def lease(self, media_id):
        with self._lock:
            record = self.get(media_id)
            self._leases[media_id] = self._leases.get(media_id, 0) + 1
        try:
            yield record
        finally:
            with self._lock:
                self._leases[media_id] -= 1
                self._cleanup(media_id)

    def public(self, record):
        result = {k: v for k, v in record.items() if k not in ('artifacts', 'playback', 'metadata_hints', 'metadata_overrides')}
        result['scan'] = public_scan(result.get('scan'))
        if record.get('artwork_status') == 'ready':
            result['artwork_url'] = f'/api/media/{record["id"]}/artwork'
        return result

    @staticmethod
    def _playlist(row):
        result = dict(id=row['id'], name=row['name'], media_ids=json.loads(row['media_ids']), revision=row['revision'])
        result['etag'] = f'"playlist-{result["id"]}-{result["revision"]}"'
        return result

    def playlists(self):
        with self._lock:
            return [self._playlist(row) for row in self._db.execute('SELECT * FROM local_playlists ORDER BY position')]

    def get_playlist(self, playlist_id):
        self._id(playlist_id)
        with self._lock:
            row = self._db.execute('SELECT * FROM local_playlists WHERE id=?', (playlist_id,)).fetchone()
            if row is None:
                raise MediaError('Unknown playlist ID')
            return self._playlist(row)

    def _playlist_members(self, ids):
        if not isinstance(ids, list) or len(ids) > MAX_FILES:
            raise MediaError('A playlist can contain at most 200 tracks')
        for media_id in ids:
            self._id(media_id)
        if len(set(ids)) != len(ids):
            raise MediaError('Repeated media IDs are not allowed')
        current = {row[0] for row in self._db.execute('SELECT id FROM media WHERE position IS NOT NULL')}
        if not set(ids).issubset(current):
            raise MediaError('A selected track is no longer in the library')
        return ids

    def create_playlist(self, name, media_ids=None):
        name = playlist_name(name)
        with self._lock:
            ids = self._playlist_members([] if media_ids is None else media_ids)
            if self._db.execute('SELECT COUNT(*) FROM local_playlists').fetchone()[0] >= 200:
                raise MediaError('The library can contain at most 200 saved playlists')
            playlist_id = uuid.uuid4().hex
            position = self._db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM local_playlists').fetchone()[0]
            with self._db:
                self._db.execute('INSERT INTO local_playlists VALUES(?,?,?,?,?)',
                    (playlist_id, name, json.dumps(ids), 1, position))
            return self.get_playlist(playlist_id)

    def update_playlist(self, playlist_id, changes, expected):
        with self._lock:
            current = self.get_playlist(playlist_id)
            if expected != current['etag']:
                raise PlaylistConflictError('Playlist changed; refresh before editing')
            if not changes or set(changes) - {'name', 'media_ids'}:
                raise MediaError('Provide a playlist name or media IDs')
            name = playlist_name(changes['name']) if 'name' in changes else current['name']
            ids = self._playlist_members(changes['media_ids']) if 'media_ids' in changes else current['media_ids']
            if name != current['name'] or ids != current['media_ids']:
                with self._db:
                    self._db.execute('UPDATE local_playlists SET name=?,media_ids=?,revision=revision+1 WHERE id=?',
                        (name, json.dumps(ids), playlist_id))
            return self.get_playlist(playlist_id)

    def delete_playlist(self, playlist_id, expected):
        with self._lock:
            current = self.get_playlist(playlist_id)
            if expected != current['etag']:
                raise PlaylistConflictError('Playlist changed; refresh before deleting')
            with self._db:
                self._db.execute('DELETE FROM local_playlists WHERE id=?', (playlist_id,))
                saved = self._db.execute('SELECT value FROM playback_state WHERE id=1').fetchone()
                if saved:
                    state = json.loads(saved[0])
                    if state.get('playlist_id') == playlist_id:
                        state['playlist_id'] = None
                        self._db.execute('UPDATE playback_state SET value=? WHERE id=1', (json.dumps(state),))

    def edit_metadata(self, media_id, changes):
        from metadata import normalize_metadata
        allowed = {'title', 'artist', 'album', 'genre', 'year', 'track'}
        if not changes or set(changes) - allowed:
            raise MediaError('Provide supported music details to edit')
        for key, value in changes.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
                raise MediaError('Music details must be text or a year number')
            if value is not None and len(str(value)) > 512:
                raise MediaError('Music details must contain at most 512 characters')
            if key != 'year' and value is not None and not isinstance(value, str):
                raise MediaError('Music details must be text')
        with self._lock:
            record = self.get(media_id)
            if media_id in self._working:
                raise MediaBusyError('This track is being processed; wait before editing music details')
            self._playlist_members([media_id])
            merged = {**record, **changes}
            if 'year' in changes:
                value = str(changes['year']).strip() if changes['year'] is not None else ''
                if value and (not re.fullmatch(r'[0-9]{4}', value) or int(value) < 1):
                    raise MediaError('Year must be four digits from 0001 to 9999, or empty')
                merged['date'] = value or None
                merged['year'] = value or None
            normalized = normalize_metadata(merged, record['name'])
            if 'track' in changes and str(changes['track'] or '').strip() and 'track' not in normalized:
                raise MediaError('Track number must be positive, optionally followed by a total such as 2/10')
            keys = set(changes) | ({'date'} if 'year' in changes else set())
            values = {key: normalized.get(key) for key in keys}
            overrides = {**record.get('metadata_overrides', {}), **values}
            if all(record.get(key) == value for key, value in values.items()) and overrides == record.get('metadata_overrides', {}):
                return record
            return self.update(media_id, **values, metadata_overrides=overrides,
                metadata_revision=record.get('metadata_revision', 0) + 1)

    def playback_state(self, changes=None):
        with self._lock:
            row = self._db.execute('SELECT value FROM playback_state WHERE id=1').fetchone()
            state = json.loads(row[0]) if row else dict(media_id=None, position_seconds=0,
                volume=0.8, shuffle=False, repeat='off', playlist_id=None)
            if changes is not None:
                if changes.get('playlist_id') is not None:
                    self.get_playlist(changes['playlist_id'])
                state.update(changes)
                self._db.execute('INSERT INTO playback_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (json.dumps(state),))
                self._db.commit()
            state['playlist'] = self.get_playlist(state['playlist_id']) if state.get('playlist_id') else None
            return state
