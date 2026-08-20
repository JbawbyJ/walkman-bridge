"""
In-memory job store. Single-user local tool — process restart wipes state,
which is fine.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class Job:
    job_id: str
    total_files: int
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    message: str = "Queued"
    created_at: float = field(default_factory=time.time)
    _log: deque = field(default_factory=lambda: deque(maxlen=200))

    def log(self, line: str) -> None:
        self._log.append(f"[{time.strftime('%H:%M:%S')}] {line}")

    def log_tail(self, n: int) -> list[str]:
        return list(self._log)[-n:]

    def set_status(self, status: JobStatus, message: str) -> None:
        self.status = status
        self.message = message
        self.log(message)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = RLock()

    def create(self, job_id: str, total_files: int) -> Job:
        with self._lock:
            job = Job(job_id=job_id, total_files=total_files)
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


job_store = JobStore()
