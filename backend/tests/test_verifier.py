"""Independent integration regressions against the approved persistence contract."""
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

from application import create_app
from jobs import JobStatus
from media_store import MediaError, MediaStore
import media_service
import jsymphonic
from test_api import context, imported, TOKEN, ORIGIN


def test_recovered_uncertain_write_blocks_duplicate_transfer(context, tmp_path):
    media_id = imported(context)['files'][0]['media_id']
    pending = context.app.state.jobs.create('crash-during-device-write', 1, kind='transfer')
    pending.set_files([dict(file_id=media_id, media_id=media_id, name='track.wav',
                            state='transferring')], phase='device_writing')
    pending.set_status(JobStatus.RUNNING, 'Writing to Walkman')

    # Reopen only persisted state, as a fresh backend after an ungraceful exit.
    restarted = create_app(product='bridge', data_dir=tmp_path / 'state', token=TOKEN,
        origin=ORIGIN, scanner=context.scanner, device_api=context.api)
    with TestClient(restarted, base_url=ORIGIN, headers={'X-NightOps-Token': TOKEN}) as client:
        recovered = client.get('/api/jobs/crash-during-device-write').json()
        assert recovered['files'][0]['state'] == 'unknown'
        assert recovered['needs_reconcile'] is True
        response = client.post('/api/transfers', json={'media_ids': [media_id]})
        assert response.status_code == 409, response.text
        assert context.calls['add'] == 0


def test_threat_retains_blocked_file_outcome(context, monkeypatch):
    original_scan = context.scanner.scan_audio
    def threat(path):
        result = original_scan(path).to_dict()
        result.update(ok=False, state='THREAT', reason='Threat detected',
                      reason_code='defender_threat', defender_status='threat')
        return SimpleNamespace(to_dict=lambda: result)
    monkeypatch.setattr(context.scanner, 'scan_audio', threat)
    job = imported(context)
    assert job['status'] == 'failed'
    assert job['files'][0]['state'] == 'blocked'
    assert job['files'][0]['reason_code'] == 'defender_threat'
    assert context.calls['convert'] == context.calls['add'] == 0


def test_rescan_clears_selected_playback_artifact(context):
    media_id = imported(context)['files'][0]['media_id']
    base = f'/api/media/{media_id}'
    prepared = context.client.post(base + '/prepare', json={'format': 'flac'})
    assert prepared.status_code == 200
    assert context.client.get(base + '/stream').status_code == 200
    store = context.app.state.store
    record = store.get(media_id)
    record['artifacts']['playback']['scan']['sha256'] = '0' * 64
    store.update(media_id, artifacts=record['artifacts'])
    assert context.client.get(base + '/stream').status_code == 423

    rescanned = context.client.post(base + '/rescan')
    job = context.client.get('/api/jobs/' + rescanned.json()['job_id']).json()
    assert job['status'] == 'done'
    assert context.client.get(base + '/stream').status_code == 200


def test_concurrent_prepare_failure_cannot_remove_successful_derivative(context, monkeypatch):
    media_id = imported(context)['files'][0]['media_id']
    first_entered, second_entered = threading.Event(), threading.Event()
    release_first, release_second = threading.Event(), threading.Event()
    counter_lock = threading.Lock()
    invocation = 0
    def convert(source, output, kind, maximum, metadata=None):
        nonlocal invocation
        with counter_lock:
            invocation += 1
            current = invocation
        if current == 1:
            first_entered.set()
            assert release_first.wait(10)
            output.write_bytes(b'valid generated artifact')
            return output
        second_entered.set()
        assert release_second.wait(10)
        raise RuntimeError('Second encoder failed')
    monkeypatch.setattr(media_service, 'convert_managed', convert)
    results = []
    def prepare():
        response = context.client.post(f'/api/media/{media_id}/prepare', json={'format': 'flac'})
        if response.status_code == 409:
            return  # Rejecting overlapping work is also a valid implementation.
        assert response.status_code == 200, response.text
        result = context.client.get('/api/jobs/' + response.json()['job_id']).json()
        results.append(result)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(prepare)
        assert first_entered.wait(10)
        second = workers.submit(prepare)
        try:
            # If work is serialized the second cannot enter the encoder. If it
            # overlaps, make its failure happen after the first reports success.
            deadline = time.monotonic() + 1
            while not second.done() and not second_entered.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            release_first.set()
            first.result(timeout=10)
        finally:
            release_first.set()
            release_second.set()
        second.result(timeout=10)
    assert any(job['status'] == 'done' for job in results)
    record = context.app.state.store.get(media_id)
    assert 'playback' in record['artifacts'], record
    assert context.client.get(f'/api/media/{media_id}/stream').status_code == 200


def test_restart_orphan_bytes_cannot_escape_cache_quota(tmp_path):
    root = tmp_path / 'cache'
    orphan = root / ('a' * 32)
    orphan.mkdir(parents=True)
    (orphan / 'source.wav').write_bytes(b'x' * 24)
    store = MediaStore(tmp_path / 'state.sqlite', root, limit=32)
    try:
        store.import_file('new.wav', io.BytesIO(b'y' * 16))
    except MediaError:
        pass
    actual_bytes = sum(path.stat().st_size for path in root.rglob('*') if path.is_file())
    assert actual_bytes <= store.limit, (actual_bytes, store.used_bytes())


def test_queued_transfer_rechecks_uncertainty_before_another_write(context):
    media_id = imported(context)['files'][0]['media_id']
    writing = threading.Event()
    release = threading.Event()
    original_add = context.api.add_tracks
    writes = []
    def add(mount, paths, callback):
        writes.append(tuple(paths))
        if len(writes) == 1:
            writing.set()
            assert release.wait(10)
            raise jsymphonic.JSymphonicError('Interrupted after device write began', needs_reconcile=True)
        return original_add(mount, paths, callback)
    context.api.add_tracks = add
    def transfer():
        return context.client.post('/api/transfers', json={'media_ids': [media_id]})
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(transfer)
        assert writing.wait(10)
        second = workers.submit(transfer)
        try:
            deadline = time.monotonic() + 5
            while not second.done() and time.monotonic() < deadline:
                if context.app.state.coordinator.snapshot()['pending']:
                    break
                time.sleep(0.01)
        finally:
            release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert len(writes) == 1, 'A second admitted transfer wrote after the first became uncertain'


def test_playback_lease_outlives_stream_and_stale_release_preserves_new_selection(context):
    job = imported(context, ('first.wav', 'second.wav'))
    first, second = [row['media_id'] for row in job['files']]
    store = context.app.state.store
    first_path, second_path = (store.path(store.get(media_id)) for media_id in (first, second))
    route = '/api/playback/lease'
    try:
        assert context.client.post(route, json={'media_id': first}).status_code == 200
        # Completing an entire HTTP body must not release the selected-track lease.
        assert context.client.get(f'/api/media/{first}/stream').status_code == 200
        assert context.client.delete(f'/api/queue/items/{first}').status_code == 200
        assert first_path.exists()
        assert context.client.get(f'/api/media/{first}/stream').status_code == 200

        assert context.client.post(route, json={'media_id': second}).status_code == 200
        assert not first_path.exists()
        assert context.client.request('DELETE', route, json={'media_id': first}).status_code == 200
        assert context.client.delete(f'/api/queue/items/{second}').status_code == 200
        assert second_path.exists(), 'Stale release ended the newer selection lease'
        assert context.client.post('/api/shutdown/drain').json()['busy'] is True
        assert context.client.request('DELETE', route, json={'media_id': second}).status_code == 200
        assert context.client.get('/api/engine-busy').json()['busy'] is False
        assert not second_path.exists()
    finally:
        for media_id in (first, second):
            context.client.request('DELETE', route, json={'media_id': media_id})
