"""SQLite JobStore across a simulated process restart, including hostile database files."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from jobs import JobStatus, JobStore


def _close(store):
    store.close()


def test_created_running_and_completed_jobs_survive_close_and_reopen(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    created = store.create('created', 1, kind='import')
    created.set_files([{'file_id': 'c', 'name': 'c.mp3', 'state': 'queued'}])
    running = store.create('running', 2, kind='transfer')
    running.set_status(JobStatus.RUNNING, 'Copying')
    running.progress = 0.4
    running.touch()
    running.set_phase('device_writing')
    running.set_files([
        {'file_id': 'a', 'name': 'a.mp3', 'state': 'transferring', 'downloaded_bytes': 40, 'total_bytes': 100},
        {'file_id': 'b', 'name': 'b.mp3', 'state': 'queued'},
    ])
    done = store.create('done', 1, kind='upload')
    done.set_status(JobStatus.DONE, 'Completed 1 of 1 files')
    done.progress = 1
    done.touch()
    done.set_files([{'file_id': 'd', 'name': 'd.mp3', 'state': 'transferred'}])
    failed = store.create('failed', 1)
    failed.set_status(JobStatus.FAILED, 'disk full')
    partial = store.create('partial', 2, kind='transfer')
    partial.set_status(JobStatus.PARTIAL, 'Completed 1 of 2 files')
    partial.progress = 1
    partial.touch()
    _close(store)

    reopened = JobStore(db)
    try:
        assert reopened.get('created').status == JobStatus.PENDING
        assert reopened.get('created').files[0]['state'] == 'queued'
        running_job = reopened.get('running')
        assert running_job.status == JobStatus.RUNNING
        assert running_job.progress == pytest.approx(0.4)
        assert running_job.phase == 'device_writing'
        assert running_job.files[0]['downloaded_bytes'] == 40
        assert running_job.files[0]['total_bytes'] == 100
        assert reopened.get('done').status == JobStatus.DONE
        assert reopened.get('done').files[0]['state'] == 'transferred'
        assert reopened.get('failed').status == JobStatus.FAILED
        assert reopened.get('partial').status == JobStatus.PARTIAL
        assert reopened.recover_interrupted() == 2
        assert reopened.get('created').status == JobStatus.INTERRUPTED
        assert reopened.get('created').files[0]['state'] == 'interrupted'
        recovered = reopened.get('running')
        assert recovered.status == JobStatus.INTERRUPTED
        assert recovered.needs_reconcile is True
        assert [row['state'] for row in recovered.files] == ['unknown', 'interrupted']
        assert recovered.files[0]['downloaded_bytes'] == 40
        assert reopened.get('done').status == JobStatus.DONE
        assert reopened.get('failed').status == JobStatus.FAILED
        assert reopened.get('failed').message == 'disk full'
        assert reopened.get('partial').status == JobStatus.PARTIAL
        assert reopened.recover_interrupted() == 0
    finally:
        _close(reopened)

    checked = JobStore(db)
    try:
        assert checked.get('running').status == JobStatus.INTERRUPTED
        assert checked.get('running').files[0]['state'] == 'unknown'
        assert checked.get('done').to_dict()['status'] == 'done'
        assert checked.get('partial').status == JobStatus.PARTIAL
    finally:
        _close(checked)


def test_crash_after_commit_reopens_the_same_in_progress_job(tmp_path):
    """A new JobStore on the same file is the process-restart stand-in."""
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    job = store.create('inflight', 1, kind='transfer')
    job.set_status(JobStatus.RUNNING, 'Writing to Walkman')
    job.set_phase('transferring')
    job.set_files([{'file_id': 'f', 'name': 'song.mp3', 'state': 'transferring'}])
    job.progress = 0.25
    job.touch()
    _close(store)

    crashed = JobStore(db)
    try:
        loaded = crashed.get('inflight')
        assert loaded.status == JobStatus.RUNNING
        assert loaded.progress == 0.25
        assert crashed.recover_interrupted() == 1
        assert crashed.get('inflight').status == JobStatus.INTERRUPTED
        assert crashed.get('inflight').needs_reconcile is True
        assert crashed.get('inflight').files[0]['state'] == 'unknown'
    finally:
        _close(crashed)


def test_concurrent_updates_survive_reopen(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    errors = []

    def worker(index):
        try:
            job = store.create(f'job-{index}', index + 1, kind='transfer')
            job.set_files([{'file_id': f'f-{index}', 'name': f'{index}.mp3', 'state': 'queued'}])
            job.progress = index / 10
            job.touch()
            if index % 2:
                job.set_status(JobStatus.RUNNING, f'working {index}')
            else:
                job.set_status(JobStatus.DONE, f'done {index}')
                job.progress = 1
                job.touch()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append((index, type(exc).__name__, str(exc)))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    _close(store)

    reopened = JobStore(db)
    try:
        assert len(reopened._jobs) == 12
        for index in range(12):
            job = reopened.get(f'job-{index}')
            assert job is not None
            assert job.total_files == index + 1
            assert job.files[0]['file_id'] == f'f-{index}'
            if index % 2:
                assert job.status == JobStatus.RUNNING
                assert job.progress == pytest.approx(index / 10)
            else:
                assert job.status == JobStatus.DONE
                assert job.progress == 1
    finally:
        _close(reopened)


def test_locked_database_fails_cleanly_and_keeps_committed_rows(tmp_path, monkeypatch):
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    store.create('keep', 3).set_status(JobStatus.DONE, 'kept')
    _close(store)
    locker = sqlite3.connect(db)
    locker.execute('BEGIN EXCLUSIVE')
    real_connect = sqlite3.connect

    def fast_connect(path, *args, **kwargs):
        kwargs['timeout'] = 0.05
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', fast_connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            JobStore(db)
    finally:
        locker.rollback()
        locker.close()
    restored = JobStore(db)
    try:
        job = restored.get('keep')
        assert job.status == JobStatus.DONE
        assert job.total_files == 3
        assert job.message == 'kept'
    finally:
        _close(restored)


def test_corrupt_database_file_is_not_replaced(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    payload = b'this is not a sqlite database!!!!'
    db.write_bytes(payload)
    with pytest.raises(sqlite3.DatabaseError):
        JobStore(db)
    assert db.read_bytes() == payload


def test_missing_database_starts_empty_without_raising(tmp_path):
    db = tmp_path / 'missing' / 'jobs.sqlite'
    assert not db.exists()
    store = JobStore(db)
    try:
        assert db.is_file()
        assert store.latest() is None
        store.create('fresh', 1)
        assert store.get('fresh').status == JobStatus.PENDING
    finally:
        _close(store)
    reopened = JobStore(db)
    try:
        assert reopened.get('fresh').total_files == 1
    finally:
        _close(reopened)


def test_directory_path_is_rejected_without_creating_a_store(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        JobStore(tmp_path)


def test_schema_mismatch_preserves_the_original_rows(tmp_path):
    db = tmp_path / 'schema.sqlite'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, blob TEXT)')
        connection.execute("INSERT INTO jobs VALUES (1, 'keep-me')")
    before = db.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        JobStore(db)
    assert b'keep-me' in db.read_bytes()
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT blob FROM jobs').fetchone()[0] == 'keep-me'
    assert b'keep-me' in before


def test_invalid_status_does_not_wipe_sibling_jobs(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    store.create('good', 4).set_status(JobStatus.DONE, 'safe')
    _close(store)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO jobs (job_id, total_files, status, progress, message, created_at, log, "
            "kind, phase, needs_reconcile, files) VALUES "
            "('bad', 1, 'not-a-status', 0, 'x', 1, '[]', 'upload', 'queued', 0, '[]')"
        )
    with pytest.raises(ValueError):
        JobStore(db)
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT status, message, total_files FROM jobs WHERE job_id='good'"
        ).fetchone()
    assert row == ('done', 'safe', 4)


def test_corrupt_json_columns_do_not_drop_the_job_row(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    job = store.create('row', 2, kind='transfer')
    job.set_status(JobStatus.DONE, 'kept')
    job.progress = 0.75
    job.touch()
    _close(store)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET log='{', files='\"nope\"' WHERE job_id='row'")
    reopened = JobStore(db)
    try:
        loaded = reopened.get('row')
        assert loaded.status == JobStatus.DONE
        assert loaded.progress == 0.75
        assert loaded.message == 'kept'
        assert loaded.total_files == 2
        assert loaded.files == []
        assert loaded.log_tail(5) == []
    finally:
        _close(reopened)


def test_duplicate_job_id_does_not_replace_the_original(tmp_path):
    store = JobStore(tmp_path / 'jobs.sqlite')
    try:
        original = store.create('same', 2)
        original.set_status(JobStatus.DONE, 'original')
        with pytest.raises(ValueError, match='already exists'):
            store.create('same', 9)
        assert store.get('same').total_files == 2
        assert store.get('same').message == 'original'
    finally:
        _close(store)


@pytest.mark.xfail(reason=(
    'BUG: JobStore.create after close() returns a job and does not raise, but _upsert '
    'drops the write because the connection is already None. A caller observes success '
    'for a job that does not survive reopen '
    '(test_closed_job_store_does_not_report_a_persisted_create).'
))
def test_closed_job_store_does_not_report_a_persisted_create(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    store = JobStore(db)
    store.create('kept', 1).set_status(JobStatus.DONE, 'kept')
    _close(store)
    with pytest.raises(Exception):
        store.create('after-close', 1)
    reopened = JobStore(db)
    try:
        assert reopened.get('kept').status == JobStatus.DONE
        assert reopened.get('after-close') is None
    finally:
        _close(reopened)


@pytest.mark.xfail(reason=(
    'BUG: crash recovery on a second JobStore marks an in-progress device write interrupted, '
    'then a stale touch() from the original store overwrites that row and the job looks '
    'running/transferring again. Concurrent writers can discard recovered state '
    '(test_stale_store_cannot_overwrite_crash_recovery).'
))
def test_stale_store_cannot_overwrite_crash_recovery(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    original = JobStore(db)
    job = original.create('job', 1, kind='transfer')
    job.set_status(JobStatus.RUNNING, 'Writing to Walkman')
    job.set_phase('device_writing')
    job.set_files([{'file_id': 'f', 'name': 'a.mp3', 'state': 'transferring'}])
    restarted = JobStore(db)
    try:
        assert restarted.recover_interrupted() == 1
        assert restarted.get('job').status == JobStatus.INTERRUPTED
        assert restarted.get('job').files[0]['state'] == 'unknown'
        original.get('job').touch()
    finally:
        _close(restarted)
        _close(original)
    checked = JobStore(db)
    try:
        recovered = checked.get('job')
        assert recovered.status == JobStatus.INTERRUPTED
        assert recovered.needs_reconcile is True
        assert recovered.files[0]['state'] == 'unknown'
    finally:
        _close(checked)
