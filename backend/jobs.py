"""
SQLite-backed job store. The in-memory dict is a cache; the database is the
source of truth across process restarts. Thread-safe via RLock because
BackgroundTasks may run off the main loop.

Path: env WALKMAN_JOBS_DB, else backend/walkman-jobs.sqlite.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import RLock


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    PARTIAL = "partial"


def _default_db_path() -> Path:
    env = os.environ.get("WALKMAN_JOBS_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "walkman-jobs.sqlite"


@dataclass
class Job:
    job_id: str
    total_files: int
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    message: str = "Queued"
    created_at: float = field(default_factory=time.time)
    _log: deque = field(default_factory=lambda: deque(maxlen=200))
    _store: JobStore | None = field(default=None, repr=False, compare=False)

    def log(self, line: str) -> None:
        self._log.append(f"[{time.strftime('%H:%M:%S')}] {line}")
        self.touch()

    def log_tail(self, n: int) -> list[str]:
        return list(self._log)[-n:]

    def set_status(self, status: JobStatus, message: str) -> None:
        self.status = status
        self.message = message
        self.log(message)

    def touch(self) -> None:
        """Persist after mutating fields that set_status/log do not cover (progress)."""
        if self._store is not None:
            self._store.save(self)


class JobStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = RLock()
        self._db_path = Path(db_path) if db_path is not None else _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # BackgroundTasks may persist from a worker thread.
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    total_files INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    message TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    log TEXT NOT NULL
                )
                """
            )
            self._conn.commit()
            self._load()

    def create(self, job_id: str, total_files: int) -> Job:
        with self._lock:
            job = Job(job_id=job_id, total_files=total_files)
            job._store = self
            self._jobs[job_id] = job
            self._upsert(job)
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def save(self, job: Job) -> None:
        with self._lock:
            job._store = self
            self._jobs[job.job_id] = job
            self._upsert(job)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _load(self) -> None:
        rows = self._conn.execute(
            "SELECT job_id, total_files, status, progress, message, created_at, log "
            "FROM jobs"
        )
        for row in rows:
            job = self._job_from_row(row)
            self._jobs[job.job_id] = job

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        try:
            lines = json.loads(row["log"])
            if not isinstance(lines, list):
                lines = []
        except (json.JSONDecodeError, TypeError):
            lines = []
        job = Job(
            job_id=row["job_id"],
            total_files=int(row["total_files"]),
            status=JobStatus(row["status"]),
            progress=float(row["progress"]),
            message=row["message"] or "",
            created_at=float(row["created_at"]),
            _log=deque((str(x) for x in lines), maxlen=200),
        )
        job._store = self
        return job

    def _upsert(self, job: Job) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO jobs (
                job_id, total_files, status, progress, message, created_at, log
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                total_files = excluded.total_files,
                status = excluded.status,
                progress = excluded.progress,
                message = excluded.message,
                created_at = excluded.created_at,
                log = excluded.log
            """,
            (
                job.job_id,
                job.total_files,
                job.status.value,
                job.progress,
                job.message,
                job.created_at,
                json.dumps(list(job._log), ensure_ascii=False),
            ),
        )
        self._conn.commit()


job_store = JobStore()
