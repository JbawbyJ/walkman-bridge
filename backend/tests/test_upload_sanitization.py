"""Adversarial upload names and payloads. Managed bytes must stay in the cache."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adversarial_helpers import ORIGIN, TOKEN, ClaimedSize, HashScanner, assert_store_contained
from application import create_app
from media_store import MAX_FILE, MIMES, MediaError, MediaStore
import media_service
from transcode import AudioError


HOSTILE_NAMES = [
    '../etc/passwd',
    '../../escape.mp3',
    '/etc/passwd',
    '/tmp/absolute.mp3',
    '..\\..\\Windows\\system.ini',
    '..\\secret.mp3',
    'C:\\Windows\\system32\\evil.mp3',
    'C:/Windows/system32/evil.mp3',
    'C:foo.mp3',
    '\\\\server\\share\\evil.mp3',
    '//server/share/evil.mp3',
    '..\u2215..\u2215etc\u2215passwd.mp3',          # division slash
    '..\uff0f..\uff0fetc\uff0fpasswd.mp3',          # fullwidth solidus
    '\uff0e\uff0e/\uff0e\uff0e/etc/passwd.mp3',     # fullwidth dots, real slashes
    'CON',
    'CON.mp3',
    'NUL.mp3',
    'PRN.wav',
    'AUX.txt',
    'COM1.mp3',
    'LPT1.mp3',
    '.hidden.mp3',
    '.bashrc',
    '',
    '.',
    '..',
    '   ',
    'song.mp3.exe',
    'song.mp3.jpg',
    'song.MP3',
    'song.mp3 ',
    'song.mp3.',
    'song.mp3:secret',
    'file.mp3\n.exe',
    '%2e%2e/%2e%2e/etc/passwd.mp3',
    'foo\x00/bar.mp3',
    ('a' * 300) + '.mp3',
    ('\u00e9' * 180) + '.flac',
    'safe-name.ogg',
]


def _store(tmp_path, limit=10_000_000):
    return MediaStore(tmp_path / 'state.db', tmp_path / 'cache', limit=limit)


@pytest.mark.parametrize('name', HOSTILE_NAMES)
def test_hostile_upload_names_never_escape_the_cache(tmp_path, name):
    store = _store(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    payload = b'abcd'
    try:
        record = store.import_file(name, io.BytesIO(payload))
    except MediaError:
        assert store.queue() == []
        assert assert_store_contained(store) == []
    else:
        path = store.path(record)
        assert path.read_bytes() == payload
        assert path.is_relative_to(store.root)
        assert record['status'] == 'queued'
        assert record['mime'] == MIMES.get(Path(record['name']).suffix.lower(), 'application/octet-stream')
        suffix = path.suffix
        assert suffix == '.bin' or suffix in MIMES
        if Path(name.replace('\\', '/')).name.lower().endswith(tuple(MIMES)):
            # A trailing allowed extension on the final path segment may be kept.
            assert suffix in MIMES or suffix == '.bin'
    assert list(outside.iterdir()) == []
    assert_store_contained(store)
    # Display-name tricks must not create a second file next to the source artifact.
    for record in store.queue():
        folder = store.root / record['id']
        assert [item.name for item in folder.iterdir()] == [store.path(record).name]


def test_duplicate_sanitized_names_do_not_overwrite(tmp_path):
    store = _store(tmp_path)
    first = store.import_file('../same.mp3', io.BytesIO(b'one'))
    second = store.import_file('..\\same.mp3', io.BytesIO(b'two-bytes'))
    third = store.import_file('same.mp3', io.BytesIO(b'three'))
    assert len({first['id'], second['id'], third['id']}) == 3
    assert first['name'] == second['name'] == third['name'] == 'same.mp3'
    assert store.path(first).read_bytes() == b'one'
    assert store.path(second).read_bytes() == b'two-bytes'
    assert store.path(third).read_bytes() == b'three'
    assert_store_contained(store)


def test_repeated_media_id_does_not_destroy_the_first_file(tmp_path):
    store = _store(tmp_path)
    media_id = 'ab' * 16
    first = store.import_file('one.mp3', io.BytesIO(b'hello'), media_id=media_id)
    with pytest.raises(Exception):
        store.import_file('two.mp3', io.BytesIO(b'world'), media_id=media_id)
    assert store.path(first).read_bytes() == b'hello'
    assert store.get(media_id)['size_bytes'] == 5
    assert_store_contained(store)


def test_symlink_media_directory_cannot_redirect_the_write(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    media_id = 'cd' * 16
    (store.root / media_id).symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception):
        store.import_file('song.mp3', io.BytesIO(b'hello'), media_id=media_id)
    assert list(outside.iterdir()) == []
    assert_store_contained(store)


def test_oversized_claim_is_rejected_without_a_partial_file(tmp_path):
    store = _store(tmp_path)
    before = list(store.root.iterdir())
    with pytest.raises(MediaError, match='500 MiB'):
        store.import_file('big.mp3', ClaimedSize(b'tiny', MAX_FILE + 1))
    assert list(store.root.iterdir()) == before
    assert store.queue() == []


def test_non_string_filename_does_not_write_outside(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    with pytest.raises(Exception):
        store.import_file(b'../escaped.mp3', io.BytesIO(b'abcd'))
    assert list(outside.iterdir()) == []
    assert store.queue() == []
    assert_store_contained(store)


def test_client_mime_cannot_promote_a_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'analyze_cleared_audio', lambda path: {})
    app = create_app(product='player', data_dir=tmp_path / 'state', token=TOKEN,
                     origin=ORIGIN, scanner=HashScanner())
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.post('/api/media/import', files=[
            ('files', ('notes.exe', b'not-audio', 'audio/mpeg')),
            ('files', ('song.mp3', b'ID3-pretend', 'text/plain')),
            ('files', ('../../../../tmp/pwned.wav', b'RIFF', 'application/x-msdownload')),
        ])
        assert response.status_code == 200, response.text
        job = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert job['status'] == 'done'
        store = app.state.store
        by_name = {store.get(row['media_id'])['name']: store.get(row['media_id']) for row in job['files']}
        assert by_name['notes.exe']['mime'] == 'application/octet-stream'
        assert store.path(by_name['notes.exe']).suffix == '.bin'
        assert by_name['song.mp3']['mime'] == 'audio/mpeg'
        assert store.path(by_name['song.mp3']).suffix == '.mp3'
        assert by_name['pwned.wav']['name'] == 'pwned.wav'
        relative = store.path(by_name['pwned.wav']).relative_to(store.root)
        assert relative.parts == (by_name['pwned.wav']['id'], 'source.wav')
        assert_store_contained(store)


@pytest.mark.xfail(reason=(
    'BUG: MediaStore.import_file persists a filename containing a NUL byte instead of '
    'raising MediaError. The managed bytes stay inside the cache, but the queue name '
    'still contains the NUL (test_null_byte_filename_is_rejected).'
))
def test_null_byte_filename_is_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(MediaError):
        store.import_file('track\x00.mp3', io.BytesIO(b'abcd'))
    assert store.queue() == []
    assert_store_contained(store)


@pytest.mark.xfail(reason=(
    'BUG: a zero-byte import that fails analysis is still returned by the stream route '
    'with HTTP 200. Scan clearance is left in place after status is set to failed, so '
    'the empty file looks like a complete audio response '
    '(test_zero_byte_import_is_not_served_as_complete).'
))
def test_zero_byte_import_is_not_served_as_complete(tmp_path, monkeypatch):
    def reject_empty(path):
        raise AudioError('Audio format could not be decoded')

    monkeypatch.setattr(media_service, 'analyze_cleared_audio', reject_empty)
    app = create_app(product='player', data_dir=tmp_path / 'state', token=TOKEN,
                     origin=ORIGIN, scanner=HashScanner())
    with TestClient(app, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        response = client.post('/api/media/import', files={'files': ('empty.mp3', b'', 'audio/mpeg')})
        assert response.status_code == 200, response.text
        job = client.get('/api/jobs/' + response.json()['job_id']).json()
        assert job['status'] == 'failed'
        assert job['files'][0]['state'] == 'failed'
        media_id = job['files'][0]['media_id']
        store = app.state.store
        record = store.get(media_id)
        assert record['status'] == 'failed'
        assert record['size_bytes'] == 0
        assert store.path(record).stat().st_size == 0
        assert store.path(record).is_relative_to(store.root)
        stream = client.get(f'/api/media/{media_id}/stream')
        assert stream.status_code != 200
        assert stream.content == b''
