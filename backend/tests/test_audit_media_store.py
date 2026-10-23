"""A held or vanished managed file never blocks startup, a committed removal or the quota.

Windows refuses to delete a file another process (an orphaned ffmpeg, the indexer, antivirus)
still holds; these tests reproduce that with a failing rmtree/unlink on synthetic files.
"""
import io
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest

import media_store
from media_store import MediaError, MediaStore

REAL_RMTREE = shutil.rmtree


def held_rmtree(root, keep='source.wav'):
    """Remove what it can under the cache, then fail on the held file like WinError 32."""
    def rmtree(path, *args, **kwargs):
        path = Path(path)
        if not path.is_relative_to(root):
            return REAL_RMTREE(path, *args, **kwargs)
        for child in sorted(path.rglob('*'), reverse=True):
            if child.is_file() and child.name != keep:
                child.unlink()
        raise PermissionError(13, 'The process cannot access the file because it is being used by another process', str(path))
    return rmtree


def tombstone(db, media_id):
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('UPDATE media SET position=NULL WHERE id=?', (media_id,))


def position(store, media_id):
    row = store._db.execute('SELECT position FROM media WHERE id=?', (media_id,)).fetchone()
    return 'missing' if row is None else row[0]


def test_held_tombstoned_folder_does_not_stop_startup_and_is_retried(tmp_path, monkeypatch):
    db, root = tmp_path / 'state.db', tmp_path / 'cache'
    store = MediaStore(db, root)
    keep = store.import_file('keep.wav', io.BytesIO(b'keep'))
    gone = store.import_file('gone.wav', io.BytesIO(b'gone'))
    folder = store.path(gone).parent
    store.update(gone['id'], artifacts={**gone['artifacts'],
        'playback': dict(name='playback.flac', size_bytes=4, sha256='0' * 64, scan=None)})
    (folder / 'playback.flac').write_bytes(b'flac')
    store._db.close()
    # A previous process committed the removal and exited before its lease release.
    tombstone(db, gone['id'])
    monkeypatch.setattr(media_store.shutil, 'rmtree', held_rmtree(root.absolute()))
    restarted = MediaStore(db, root)
    try:
        assert position(restarted, gone['id']) is None
        assert [r['id'] for r in restarted.library()] == [keep['id']]
        assert restarted.path(keep).read_bytes() == b'keep'
        # Partly removed: the playback copy went, the held source remains for the retry.
        assert (folder / 'source.wav').exists() and not (folder / 'playback.flac').exists()
    finally:
        restarted._db.close()
    monkeypatch.undo()
    again = MediaStore(db, root)
    try:
        assert position(again, gone['id']) == 'missing'
        assert not folder.exists()
        assert [r['id'] for r in again.library()] == [keep['id']]
    finally:
        again._db.close()


def test_held_unreferenced_derivative_does_not_stop_startup_and_is_retried(tmp_path, monkeypatch):
    db, root = tmp_path / 'state.db', tmp_path / 'cache'
    store = MediaStore(db, root)
    item = store.import_file('keep.wav', io.BytesIO(b'keep'))
    stale = store.path(item).parent / 'transfer.mp3'
    stale.write_bytes(b'interrupted transfer')
    store._db.close()
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == 'transfer.mp3':
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process', str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', unlink)
    restarted = MediaStore(db, root)
    try:
        assert [r['id'] for r in restarted.queue()] == [item['id']]
        assert stale.exists()
    finally:
        restarted._db.close()
    monkeypatch.undo()
    again = MediaStore(db, root)
    try:
        assert not stale.exists()
        assert again.path(item).read_bytes() == b'keep'
    finally:
        again._db.close()


def test_held_download_workspace_and_unknown_folder_do_not_stop_startup(tmp_path, monkeypatch):
    db, root = tmp_path / 'state.db', tmp_path / 'cache'
    store = MediaStore(db, root)
    item = store.import_file('keep.wav', io.BytesIO(b'keep'))
    workspace = store.root / ('download-' + uuid.uuid4().hex)
    workspace.mkdir()
    (workspace / 'source.wav').write_bytes(b'partial download')
    unknown = store.root / uuid.uuid4().hex
    unknown.mkdir()
    (unknown / 'source.wav').write_bytes(b'interrupted import')
    store._db.close()
    monkeypatch.setattr(media_store.shutil, 'rmtree', held_rmtree(root.absolute()))
    restarted = MediaStore(db, root)
    try:
        assert [r['id'] for r in restarted.queue()] == [item['id']]
        assert (workspace / 'source.wav').exists() and (unknown / 'source.wav').exists()
    finally:
        restarted._db.close()
    monkeypatch.undo()
    again = MediaStore(db, root)
    try:
        assert not workspace.exists() and not unknown.exists()
        assert again.path(item).read_bytes() == b'keep'
    finally:
        again._db.close()


def test_file_vanishing_during_startup_walk_is_tolerated(tmp_path, monkeypatch):
    db, root = tmp_path / 'state.db', tmp_path / 'cache'
    store = MediaStore(db, root)
    workspace = store.root / ('download-' + uuid.uuid4().hex)
    workspace.mkdir()
    (workspace / 'fragment.part').write_bytes(b'partial')
    store._db.close()
    real_rglob = Path.rglob

    def rglob(self, pattern, **kwargs):
        for path in list(real_rglob(self, pattern, **kwargs)):
            if path.name == 'fragment.part':
                path.unlink()  # an orphaned downloader finished with it mid-walk
            yield path

    monkeypatch.setattr(Path, 'rglob', rglob)
    MediaStore(db, root)._db.close()
    monkeypatch.undo()
    MediaStore(db, root)._db.close()
    assert not workspace.exists()


@pytest.mark.parametrize('where', ['workspace', 'tombstone'])
def test_reparse_point_in_deferred_deletion_stays_fatal(tmp_path, monkeypatch, where):
    db, root = tmp_path / 'state.db', tmp_path / 'cache'
    store = MediaStore(db, root)
    item = store.import_file('gone.wav', io.BytesIO(b'gone'))
    if where == 'workspace':
        folder = store.root / ('download-' + uuid.uuid4().hex)
        folder.mkdir()
        (folder / 'junction').write_bytes(b'')
    else:
        folder = store.path(item).parent
        (folder / 'junction').write_bytes(b'')
    real_no_links = media_store.no_links

    def no_links(path, base):
        # A link is a security signal, never a held file to skip past.
        if Path(path).name == 'junction':
            raise MediaError('Reparse points are not allowed in managed storage')
        return real_no_links(path, base)

    monkeypatch.setattr(media_store, 'no_links', no_links)
    if where == 'tombstone':
        with pytest.raises(MediaError, match='Reparse'):
            store.remove(item['id'])
        assert position(store, item['id']) is None
    store._db.close()
    with pytest.raises(MediaError, match='Reparse'):
        MediaStore(db, root)
    assert (folder / 'junction').exists()


def test_held_removal_after_commit_does_not_raise_and_is_retried(tmp_path, monkeypatch):
    db, root = tmp_path / 'state.db', tmp_path / 'cache'
    store = MediaStore(db, root)
    leased = store.import_file('leased.wav', io.BytesIO(b'leased'))
    direct = store.import_file('direct.wav', io.BytesIO(b'direct'))
    keep = store.import_file('keep.wav', io.BytesIO(b'keep'))
    leased_folder, direct_folder = store.path(leased).parent, store.path(direct).parent
    monkeypatch.setattr(media_store.shutil, 'rmtree', held_rmtree(root.absolute()))
    # A stream or playback lease ends after DELETE /api/media/{id} committed the removal.
    with store.lease(leased['id']):
        store.remove(leased['id'])
        assert leased_folder.exists()
    # An unleased removal reports success; the commit already happened.
    store.remove(direct['id'])
    assert position(store, leased['id']) is None and position(store, direct['id']) is None
    assert [r['id'] for r in store.library()] == [r['id'] for r in store.queue()] == [keep['id']]
    assert (leased_folder / 'source.wav').exists() and (direct_folder / 'source.wav').exists()
    monkeypatch.undo()
    # The next lease release retries ...
    with store.lease(leased['id']):
        pass
    assert position(store, leased['id']) == 'missing' and not leased_folder.exists()
    store._db.close()
    # ... and so does the next start.
    restarted = MediaStore(db, root)
    try:
        assert position(restarted, direct['id']) == 'missing' and not direct_folder.exists()
        assert restarted.path(keep).read_bytes() == b'keep'
    finally:
        restarted._db.close()


@pytest.mark.parametrize('call', ['quota', 'reserve'])
def test_quota_skips_files_removed_outside_the_store_lock(tmp_path, monkeypatch, call):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache', limit=1000)
    item = store.import_file('keep.wav', io.BytesIO(b'keep'))
    workspace = store.root / ('download-' + uuid.uuid4().hex)
    workspace.mkdir()
    (workspace / 'fragment.part').write_bytes(b'partial')
    failed = store.path(item).parent / 'transfer.mp3'
    failed.write_bytes(b'failed')
    real_rglob, real_is_file = Path.rglob, Path.is_file

    def rglob(self, pattern, **kwargs):
        for path in list(real_rglob(self, pattern, **kwargs)):
            if path.name == 'fragment.part' and workspace.exists():
                REAL_RMTREE(workspace)  # download_workspace finishing between the walk and lstat
            yield path

    def is_file(self, **kwargs):
        found = real_is_file(self, **kwargs)
        if found and self.name == 'transfer.mp3':
            self.unlink()  # a failed derivative removed between is_file and stat
        return found

    monkeypatch.setattr(Path, 'rglob', rglob)
    monkeypatch.setattr(Path, 'is_file', is_file)
    if call == 'quota':
        assert store.used_bytes() == 4
    else:
        with store.reserve(996):
            pass
    assert not workspace.exists() and not failed.exists()
    store._db.close()
