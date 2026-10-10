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
from copy import deepcopy
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
    INTERRUPTED = "interrupted"


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
    kind: str = "upload"
    phase: str = "queued"
    needs_reconcile: bool = False
    files: list[dict] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def _guard(self):
        return self._store._lock if self._store is not None else self._lock

    def log(self, line: str) -> None:
        with self._guard():
            self._log.append(f"[{time.strftime('%H:%M:%S')}] {line}")
            self.touch()

    def log_tail(self, n: int) -> list[str]:
        with self._guard():
            return list(self._log)[-n:] if n > 0 else []

    def set_status(self, status: JobStatus, message: str) -> None:
        with self._guard():
            self.status = JobStatus(status)
            self.message = message
            self.log(message)

    def set_phase(self, phase: str) -> None:
        with self._guard():
            self.phase = phase
            self.touch()

    def set_files(self, files: list[dict], phase: str | None = None) -> None:
        """Persist a complete outcome snapshot and optional phase atomically."""
        rows = deepcopy(files)
        ids = [row.get("file_id") for row in rows]
        if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
            raise ValueError("Every job file requires a unique nonempty file_id")
        with self._guard():
            self.files = rows
            if phase is not None:
                self.phase = phase
            self.touch()

    def update_file(self, file_id: str, **changes) -> None:
        if "file_id" in changes and changes["file_id"] != file_id:
            raise ValueError("Job file identity cannot change")
        with self._guard():
            for row in self.files:
                if row["file_id"] == file_id:
                    row.update(deepcopy(changes))
                    self.touch()
                    return
            raise KeyError(file_id)

    def to_dict(self) -> dict:
        with self._guard():
            return {
                "job_id": self.job_id,
                "total_files": self.total_files,
                "status": self.status.value,
                "progress": self.progress,
                "message": self.message,
                "created_at": self.created_at,
                "log_tail": list(self._log)[-50:],
                "kind": self.kind,
                "phase": self.phase,
                "needs_reconcile": self.needs_reconcile,
                "files": deepcopy(self.files),
            }

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
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(jobs)")}
            additions = {
                "kind": "TEXT NOT NULL DEFAULT 'upload'",
                "phase": "TEXT NOT NULL DEFAULT 'queued'",
                "needs_reconcile": "INTEGER NOT NULL DEFAULT 0",
                "files": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            self._conn.commit()
            self._load()

    def create(self, job_id: str, total_files: int, kind: str = "upload") -> Job:
        with self._lock:
            if job_id in self._jobs:
                raise ValueError("Job identity already exists")
            job = Job(job_id=job_id, total_files=total_files, kind=kind)
            job._store = self
            self._jobs[job_id] = job
            self._upsert(job)
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest(self) -> Job | None:
        with self._lock:
            return max(self._jobs.values(), key=lambda job: job.created_at, default=None)

    def recover_interrupted(self) -> int:
        """Call once on startup before admitting work. Never retry device writes."""
        count = 0
        with self._lock:
            for job in self._jobs.values():
                if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                    continue
                possibly_mutating = job.phase in {"transferring", "device_writing", "deleting", "updating", "reconciling"}
                legacy_running = not job.files and job.status == JobStatus.RUNNING and job.kind in {"upload", "transfer", "delete"}
                job.needs_reconcile = job.needs_reconcile or possibly_mutating or legacy_running
                for row in job.files:
                    state = row.get("state", "queued")
                    if state in {"transferring", "unknown"}:
                        row.update(state="unknown", detail="Device outcome is unknown after interruption; verify before retrying", reason_code="interrupted_device_write")
                        job.needs_reconcile = True
                    elif state in {"queued", "downloading", "analyzing", "scanning", "awaiting_permission", "transcoding"}:
                        row.update(state="interrupted", detail="Interrupted when the application stopped", reason_code="process_interrupted")
                job.status = JobStatus.INTERRUPTED
                job.phase = "interrupted"
                job.message = "Interrupted when the application stopped"
                job._log.append(f"[{time.strftime('%H:%M:%S')}] {job.message}")
                self._upsert(job, commit=False)
                count += 1
            self._conn.commit()
        return count

    def save(self, job: Job) -> None:
        with self._lock:
            job._store = self
            self._jobs[job.job_id] = job
            self._upsert(job)

    def uncertain_media_ids(self) -> set[str]:
        """Durable uncertainty survives a crash between job and media updates."""
        with self._lock:
            return {row['media_id'] for job in self._jobs.values() if job.needs_reconcile
                    for row in job.files if row.get('media_id')}

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _load(self) -> None:
        rows = self._conn.execute(
            "SELECT job_id, total_files, status, progress, message, created_at, log, kind, phase, needs_reconcile, files "
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
        try:
            files = json.loads(row["files"])
            if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
                files = []
        except (json.JSONDecodeError, TypeError):
            files = []
        job = Job(
            job_id=row["job_id"],
            total_files=int(row["total_files"]),
            status=JobStatus(row["status"]),
            progress=float(row["progress"]),
            message=row["message"] or "",
            created_at=float(row["created_at"]),
            _log=deque((str(x) for x in lines), maxlen=200),
            kind=row["kind"],
            phase=row["phase"],
            needs_reconcile=bool(row["needs_reconcile"]),
            files=files,
        )
        job._store = self
        return job

    def _upsert(self, job: Job, commit: bool = True) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            """
            INSERT INTO jobs (
                job_id, total_files, status, progress, message, created_at, log,
                kind, phase, needs_reconcile, files
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                total_files = excluded.total_files,
                status = excluded.status,
                progress = excluded.progress,
                message = excluded.message,
                created_at = excluded.created_at,
                log = excluded.log,
                kind = excluded.kind,
                phase = excluded.phase,
                needs_reconcile = excluded.needs_reconcile,
                files = excluded.files
            """,
            (
                job.job_id,
                job.total_files,
                job.status.value,
                job.progress,
                job.message,
                job.created_at,
                json.dumps(list(job._log), ensure_ascii=False),
                job.kind,
                job.phase,
                int(job.needs_reconcile),
                json.dumps(job.files, ensure_ascii=False),
            ),
        )
        if commit:
            self._conn.commit()


# Each application factory owns its store; importing this module never creates state.
