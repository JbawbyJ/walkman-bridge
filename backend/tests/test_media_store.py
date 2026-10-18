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
