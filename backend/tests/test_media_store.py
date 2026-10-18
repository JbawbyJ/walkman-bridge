import io
import pytest
from media_store import MediaStore, MediaError


def test_duplicate_names_are_distinct_and_removal_waits_for_lease(tmp_path):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache', limit=1000)
    a = store.import_file('same.wav', io.BytesIO(b'one'))
    b = store.import_file('same.wav', io.BytesIO(b'two'))
    assert a['id'] != b['id']
    with store.lease(a['id']) as record:
        source = store.path(record)
        store.remove(a['id'])
        assert source.read_bytes() == b'one'
        assert [x['id'] for x in store.queue()] == [b['id']]
    assert not source.exists()
    assert store.path(b).read_bytes() == b'two'


def test_quota_and_exact_reorder(tmp_path):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache', limit=5)
    a = store.import_file('../../escape.wav', io.BytesIO(b'123'))
    with pytest.raises(MediaError):
        store.import_file('second.wav', io.BytesIO(b'456'))
    with pytest.raises(MediaError):
        store.reorder([])
    assert store.used_bytes() == 3
    assert store.queue()[0]['name'] == 'escape.wav'
    assert store.path(a).is_relative_to(store.root)


def test_reject_unknown_or_path_media_ids(tmp_path):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    for value in ['../state.db', 'C:\\Windows', 'nope']:
        with pytest.raises(MediaError):
            store.get(value)


def test_restore_keeps_queue_and_marks_incomplete(tmp_path):
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    a = store.import_file('test.wav', io.BytesIO(b'123'))
    store.update(a['id'], status='scanning')
    restored = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    assert restored.queue()[0]['status'] == 'needs_scan'



def test_opus_imports_as_an_ogg_stream(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.opus', io.BytesIO(b'OggS' + b'\x00' * 60))
    assert record['mime'] == 'audio/ogg'
    assert record['artifacts']['source']['name'] == 'source.opus'


def test_purging_a_derivative_restores_source_playback(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    derivative = store.root / media_id / 'playback.flac'
    derivative.write_bytes(b'fLaC' + b'\x00' * 8)
    record['artifacts']['playback'] = dict(name='playback.flac', size_bytes=12, sha256='0' * 64,
                                           scan={'reason_code': 'defender_threat'})
    store.update(media_id, artifacts=record['artifacts'], playback='playback', mime='audio/flac')
    updated = store.purge_artifact(media_id, 'playback')
    assert not derivative.exists()
    assert 'playback' not in updated['artifacts']
    assert (updated['playback'], updated['mime']) == ('source', 'audio/mpeg')
    assert store.path(updated).exists()
    source_purged = store.purge_artifact(media_id, 'source')
    assert source_purged['artifacts']['source']['purged'] is True
    assert not (store.root / media_id / 'source.mp3').exists()
    with pytest.raises(MediaError):
        store.path(source_purged)
    store.purge_artifact(media_id, 'source')  # idempotent


def test_purge_persists_the_refusal_before_unlinking_and_startup_retries_a_held_file(tmp_path, monkeypatch):
    import io
    from pathlib import Path
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    derivative = store.root / media_id / 'playback.flac'
    derivative.write_bytes(b'fLaC' + b'\x00' * 8)
    record['artifacts']['playback'] = dict(name='playback.flac', size_bytes=12, sha256='0' * 64,
                                           scan={'reason_code': 'defender_threat'})
    store.update(media_id, artifacts=record['artifacts'], playback='playback', mime='audio/flac')
    real_unlink = Path.unlink

    def held_unlink(self, *args, **kwargs):
        if self.name == 'playback.flac':
            raise PermissionError(13, 'held by a reader')
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', held_unlink)
    with pytest.raises(PermissionError):
        store.purge_artifact(media_id, 'playback')
    refused = store.get(media_id)
    assert refused['artifacts']['playback']['purged'] is True and derivative.exists()
    assert (refused['playback'], refused['mime']) == ('source', 'audio/mpeg')
    with pytest.raises(MediaError):
        store.path(refused, 'playback')
    monkeypatch.setattr(Path, 'unlink', real_unlink)
    reopened = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')  # startup retries the refused unlink
    assert not derivative.exists()
    assert 'playback' not in reopened.get(media_id)['artifacts']


def test_purging_the_source_alone_resets_playback_even_while_the_derivative_remains(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    derivative = store.root / media_id / 'playback.flac'
    derivative.write_bytes(b'fLaC' + b'\x00' * 8)
    record['artifacts']['playback'] = dict(name='playback.flac', size_bytes=12, sha256='0' * 64, scan={'ok': True})
    store.update(media_id, artifacts=record['artifacts'], playback='playback', mime='audio/flac')
    updated = store.purge_artifact(media_id, 'source')
    assert (updated['playback'], updated['mime']) == ('source', 'audio/mpeg')
    assert 'playback' in updated['artifacts'] and derivative.exists()  # the derivative itself is untouched here
    with pytest.raises(MediaError):
        store.path(updated, updated['playback'])


def test_purging_artwork_drops_it_from_the_public_record(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    artwork = store.root / media_id / 'artwork.jpg'
    artwork.write_bytes(b'\xff\xd8\xff\xe0fake\xff\xd9')
    record['artifacts']['artwork'] = dict(name='artwork.jpg', size_bytes=10, sha256='0' * 64,
                                          scan={'reason_code': 'defender_threat'})
    store.update(media_id, artifacts=record['artifacts'], artwork_status='ready')
    assert 'artwork_url' in store.public(store.get(media_id))
    updated = store.purge_artifact(media_id, 'artwork')
    assert updated['artwork_status'] == 'failed'
    assert 'artwork' not in updated['artifacts'] and not artwork.exists()
    assert 'artwork_url' not in store.public(updated)


def test_purging_the_source_refuses_its_artwork_and_startup_finishes_the_purge(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    artwork = store.root / media_id / 'artwork.jpg'
    artwork.write_bytes(b'\xff\xd8\xff\xe0fake\xff\xd9')
    record['artifacts']['artwork'] = dict(name='artwork.jpg', size_bytes=10, sha256='0' * 64, scan={'ok': True})
    store.update(media_id, artifacts=record['artifacts'], artwork_status='ready')
    updated = store.purge_artifact(media_id, 'source')  # the artwork purge never happens (crash or error)
    assert updated['artwork_status'] == 'failed' and 'artwork_url' not in store.public(updated)
    assert 'artwork' in updated['artifacts'] and artwork.exists()
    reopened = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')  # startup finishes the purge
    assert not artwork.exists() and 'artwork' not in reopened.get(media_id)['artifacts']
    assert not (store.root / media_id / 'source.mp3').exists()


def test_retrying_purges_continues_past_a_held_file(tmp_path, monkeypatch):
    import io
    from pathlib import Path
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    derivative = store.root / media_id / 'transfer.mp3'
    derivative.write_bytes(b'converted')
    record['artifacts']['transfer'] = dict(name='transfer.mp3', size_bytes=9, sha256='0' * 64, scan={'ok': True})
    store.update(media_id, artifacts=record['artifacts'])
    real_unlink = Path.unlink

    def source_held(self, *args, **kwargs):
        if self.name == 'source.mp3':
            raise PermissionError(13, 'held by a reader')
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', source_held)
    with pytest.raises(PermissionError):
        store.purge_artifact(media_id, 'source')
    assert store.retry_purges(media_id) == ['source']  # the source is still held...
    assert not derivative.exists() and 'transfer' not in store.get(media_id)['artifacts']  # ...the derivative is gone
    monkeypatch.setattr(Path, 'unlink', real_unlink)
    assert store.retry_purges(media_id) == []
    assert not (store.root / media_id / 'source.mp3').exists()


def test_purging_the_source_does_not_invent_an_artwork_status(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    store.update(record['id'], artwork_status='none')
    assert store.purge_artifact(record['id'], 'source')['artwork_status'] == 'none'


def test_refusing_artifacts_persists_without_touching_files(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    flac = store.root / media_id / 'playback.flac'
    flac.write_bytes(b'fLaC' + b'\x00' * 8)
    artwork = store.root / media_id / 'artwork.jpg'
    artwork.write_bytes(b'\xff\xd8\xff\xe0fake\xff\xd9')
    record['artifacts']['playback'] = dict(name='playback.flac', size_bytes=12, sha256='0' * 64, scan={'ok': True})
    record['artifacts']['artwork'] = dict(name='artwork.jpg', size_bytes=10, sha256='0' * 64, scan={'ok': True})
    store.update(media_id, artifacts=record['artifacts'], playback='playback', mime='audio/flac', artwork_status='ready')
    refused = store.refuse_artifacts(media_id)
    assert all(info['purged'] for info in refused['artifacts'].values())
    assert (refused['playback'], refused['mime'], refused['artwork_status']) == ('source', 'audio/mpeg', 'failed')
    assert flac.exists() and artwork.exists() and 'artwork_url' not in store.public(refused)
    for artifact in ('source', 'playback', 'artwork'):
        with pytest.raises(MediaError):
            store.path(refused, artifact)
    assert store.retry_purges(media_id) == []
    assert not flac.exists() and not artwork.exists()
    assert set(store.get(media_id)['artifacts']) == {'source'}


def test_refusing_a_derivative_redirects_playback_and_leaves_the_source_alone(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    media_id = record['id']
    (store.root / media_id / 'playback.flac').write_bytes(b'fLaC' + b'\x00' * 8)
    record['artifacts']['playback'] = dict(name='playback.flac', size_bytes=12, sha256='0' * 64, scan={'ok': True})
    store.update(media_id, artifacts=record['artifacts'], playback='playback', mime='audio/flac', artwork_status='none')
    refused = store.refuse_artifacts(media_id, ['playback'])
    assert refused['artifacts']['playback']['purged'] is True
    assert refused['artifacts']['source'].get('purged') is None
    assert (refused['playback'], refused['mime'], refused['artwork_status']) == ('source', 'audio/mpeg', 'none')
    assert store.path(refused, 'source').exists()


def test_public_record_drops_artwork_url_for_a_stale_refused_artwork_entry(tmp_path):
    import io
    store = MediaStore(tmp_path / 'state.db', tmp_path / 'cache')
    record = store.import_file('song.mp3', io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 64))
    record['artifacts']['artwork'] = dict(name='artwork.jpg', size_bytes=10, sha256='0' * 64, scan={'ok': True}, purged=True)
    stale = store.update(record['id'], artifacts=record['artifacts'], artwork_status='ready')  # pre-fix row shape
    assert 'artwork_url' not in store.public(stale)
