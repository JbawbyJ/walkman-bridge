"""JobStore SQLite persistence — restart is a new JobStore on the same file."""
from __future__ import annotations

from jobs import JobStatus, JobStore


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
