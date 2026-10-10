"""JobStore SQLite persistence — restart is a new JobStore on the same file."""
from __future__ import annotations

from jobs import JobStatus, JobStore


def test_durable_file_outcomes_and_recovery(tmp_path):
    db = tmp_path / "jobs.sqlite"
    store = JobStore(db)
    job = store.create("job", 4, kind="transfer")
    job.set_files([
        {"file_id": "a", "name": "one.mp3", "state": "transferred"},
        {"file_id": "b", "name": "two.mp3", "state": "transferring"},
        {"file_id": "c", "name": "three.mp3", "state": "scanning"},
        {"file_id": "d", "name": "four.mp3", "state": "blocked"},
    ])
    job.set_phase("transferring")
    job.set_status(JobStatus.RUNNING, "Copying")
    job.update_file("a", detail="Committed", scan={"ok": True})
    store.close()
    recovered = JobStore(db)
    try:
        assert recovered.recover_interrupted() == 1
        snapshot = recovered.latest().to_dict()
        assert snapshot["status"] == "interrupted"
        assert snapshot["kind"] == "transfer"
        assert snapshot["phase"] == "interrupted"
        assert snapshot["needs_reconcile"] is True
        assert [row["state"] for row in snapshot["files"]] == ["transferred", "unknown", "interrupted", "blocked"]
        assert snapshot["files"][0]["scan"] == {"ok": True}
        assert recovered.recover_interrupted() == 0
    finally:
        recovered.close()
    reopened = JobStore(db)
    try:
        assert reopened.get("job").to_dict() == snapshot
    finally:
        reopened.close()


def test_legacy_schema_migrates_without_losing_job(tmp_path):
    import sqlite3
    db = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, total_files INTEGER NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL, log TEXT NOT NULL)")
        conn.execute("INSERT INTO jobs VALUES ('old',2,'running',0.5,'Copying',1,'[\"legacy log\"]')")
    store = JobStore(db)
    try:
        assert store.get("old").log_tail(1) == ["legacy log"]
        store.recover_interrupted()
        result = store.get("old").to_dict()
        assert result["progress"] == 0.5
        assert result["status"] == "interrupted"
        assert result["needs_reconcile"] is True
    finally:
        store.close()


def test_file_snapshot_cannot_mutate_persisted_job(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    try:
        job = store.create("job", 1, kind="import")
        rows = [{"file_id": "a", "name": "one.mp3", "state": "queued", "scan": {"ok": False}}]
        job.set_files(rows)
        rows[0]["state"] = "transferred"
        job.to_dict()["files"][0]["scan"]["ok"] = True
        assert job.to_dict()["files"][0]["state"] == "queued"
        assert job.to_dict()["files"][0]["scan"]["ok"] is False
    finally:
        store.close()


def test_job_store_sqlite_survives_new_instance(tmp_path):
    db = tmp_path / "walkman-jobs.sqlite"
    store = JobStore(db_path=db)
    try:
        job = store.create("job-1", total_files=4)
        job.set_status(JobStatus.RUNNING, "working")
        job.progress = 0.25
        job.touch()
    finally:
        store.close()

    store2 = JobStore(db_path=db)
    try:
        loaded = store2.get("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"
        assert loaded.total_files == 4
        assert loaded.status == JobStatus.RUNNING
        assert loaded.progress == 0.25
        assert loaded.message == "working"
        assert any("working" in line for line in loaded.log_tail(20))
    finally:
        store2.close()
