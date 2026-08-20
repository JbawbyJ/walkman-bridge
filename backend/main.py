"""
Walkman Bridge — local FastAPI server bridging a web dashboard to JSymphonic.

Bind to 127.0.0.1 only. Single-user local tool, no auth.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable, List

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from device import find_walkman, device_info
from transcode import normalize_to_mp3, AudioError
from jsymphonic import list_tracks, add_tracks, remove_track, JSymphonicError
from jobs import job_store, Job, JobStatus


app = FastAPI(title="Walkman Bridge", version="0.1.0")

# CORS: only the Vite dev server and same-origin production build
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORK_DIR = Path(tempfile.gettempdir()) / "walkman-bridge"
WORK_DIR.mkdir(exist_ok=True)


# --------------------------------------------------------------------------- #
# Track-list cache
# --------------------------------------------------------------------------- #

class TrackCache:
    """
    In-process cache of the device track list. Every shim `list` spawns a JVM
    and reads the whole DB, and the frontend polls /api/tracks every 3s — so
    we refetch only when empty (first call / after invalidation), when the
    mount changes, or on an explicit ?refresh=1.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mount: Path | None = None
        self._tracks: list[dict] | None = None

    def get(self, mount: Path, refresh: bool = False) -> list[dict]:
        # The lock is held across the fetch on purpose: concurrent polls wait
        # for one result instead of racing a second JVM into existence.
        with self._lock:
            if refresh or self._tracks is None or self._mount != mount:
                self._tracks = list_tracks(mount)
                self._mount = mount
            return self._tracks

    def invalidate(self) -> None:
        with self._lock:
            self._tracks = None


track_cache = TrackCache()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

class DeviceResponse(BaseModel):
    connected: bool
    mount_path: str | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    track_count: int | None = None


class TrackResponse(BaseModel):
    id: str
    title: str
    artist: str
    album: str
    duration_seconds: int | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    message: str
    log_tail: List[str]


# --------------------------------------------------------------------------- #
# Upload helpers
# --------------------------------------------------------------------------- #

def _upload_basename(filename: str | None) -> str:
    """Last path component, treating both slashes as separators.

    A browser on Windows may send `C:\\Music\\01.mp3` even when this process
    is not on Windows; Path.name would keep the whole string on POSIX.
    """
    if not filename:
        return ""
    return filename.replace("\\", "/").split("/")[-1]


def upload_destination(job_dir: Path, filename: str | None, index: int) -> Path:
    """Write each upload inside job_dir under a unique, path-safe name.

    Two dropped files often share a name (`01 Intro.mp3` from different
    albums). Using the raw filename would overwrite the first and transfer
    only one track.
    """
    raw = _upload_basename(filename)
    if not raw or raw in {".", ".."}:
        raw = f"upload-{index}"
    target = job_dir / raw
    if not target.exists():
        return target
    stem = Path(raw).stem
    suffix = Path(raw).suffix
    n = index
    candidate = job_dir / f"{stem}-{n}{suffix}"
    while candidate.exists():
        n += 1
        candidate = job_dir / f"{stem}-{n}{suffix}"
    return candidate


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/device", response_model=DeviceResponse)
async def get_device():
    mount = find_walkman()
    if not mount:
        return DeviceResponse(connected=False)
    info = device_info(mount)
    return DeviceResponse(connected=True, mount_path=str(mount), **info)


@app.get("/api/tracks", response_model=List[TrackResponse])
async def get_tracks(refresh: int = 0):
    mount = find_walkman()
    if not mount:
        raise HTTPException(status_code=404, detail="No Walkman detected")
    try:
        tracks = await asyncio.to_thread(track_cache.get, mount, bool(refresh))
    except JSymphonicError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return tracks


@app.delete("/api/tracks/{track_id}")
async def delete_track(track_id: str):
    mount = find_walkman()
    if not mount:
        raise HTTPException(status_code=404, detail="No Walkman detected")
    try:
        await asyncio.to_thread(remove_track, mount, track_id)
    except JSymphonicError as e:
        raise HTTPException(status_code=500, detail=str(e))
    track_cache.invalidate()  # ids may have shifted — next poll re-reads
    return {"ok": True}


@app.post("/api/upload")
async def upload(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
):
    mount = find_walkman()
    if not mount:
        raise HTTPException(status_code=404, detail="No Walkman detected")

    job_id = str(uuid.uuid4())
    job_dir = WORK_DIR / job_id
    job_dir.mkdir()

    # Persist uploads to disk before kicking off the background task — the
    # UploadFile handles close when the request ends.
    saved: list[Path] = []
    for f in files:
        target = upload_destination(job_dir, f.filename, len(saved))
        with target.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(target)

    job_store.create(job_id, total_files=len(saved))
    background_tasks.add_task(process_upload_job, job_id, saved, mount)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    return JobResponse(
        job_id=job.job_id,
        status=job.status.value,
        progress=job.progress,
        message=job.message,
        log_tail=job.log_tail(20),
    )


# --------------------------------------------------------------------------- #
# Background work
# --------------------------------------------------------------------------- #

def _shim_event_to_job(job: Job) -> Callable[[dict], None]:
    """Map shim protocol events into the job's log/progress/message fields."""
    def handle(event: dict) -> None:
        kind = event.get("event")
        if kind == "file":
            name = event.get("name") or "?"
            job.message = f"Transferring {name}"
            job.log(f"  > {name}")
        elif kind == "progress":
            percent = event.get("percent")
            if isinstance(percent, (int, float)):
                # Transfer is the second half of the overall job (see below).
                job.progress = 0.5 + 0.5 * (float(percent) / 100.0)
        elif kind == "step":
            job.log(f"  {event.get('step')}: {event.get('state')}")
        elif kind == "fatal":
            job.log(f"  ! {event.get('message')}")
    return handle


async def process_upload_job(job_id: str, files: List[Path], mount: Path):
    job = job_store.get(job_id)
    assert job is not None
    job.set_status(JobStatus.RUNNING, "Starting transfer")

    mp3s: list[Path] = []
    try:
        # Phase 1 — transcode everything up front (progress 0 → 0.5).
        for idx, src in enumerate(files):
            job.message = f"Transcoding {src.name}"
            job.log(f"Transcoding {src.name}")
            try:
                mp3 = await asyncio.to_thread(normalize_to_mp3, src)
            except AudioError as e:
                job.log(f"  ! transcode failed: {e}")
                continue
            mp3s.append(mp3)
            job.progress = 0.5 * (idx + 1) / len(files)

        if not mp3s:
            job.set_status(JobStatus.FAILED, "No files survived transcoding")
            return

        # Phase 2 — ONE shim invocation for the whole batch (0.5 → 1.0).
        # Per-file adds would rebuild the device DB once per track.
        job.message = "Transferring to Walkman"
        job.log(f"Transferring {len(mp3s)} file(s) to Walkman")
        try:
            await asyncio.to_thread(add_tracks, mount, mp3s, _shim_event_to_job(job))
        except JSymphonicError as e:
            job.set_status(JobStatus.FAILED, f"Transfer failed: {e}")
            return

        track_cache.invalidate()  # next /api/tracks poll re-reads the device
        job.progress = 1.0
        failed = len(files) - len(mp3s)
        if failed:
            job.set_status(
                JobStatus.PARTIAL,
                f"Transferred {len(mp3s)} of {len(files)} files "
                f"({failed} failed transcoding)",
            )
        else:
            job.set_status(JobStatus.DONE, "All files processed")
    except Exception as e:  # noqa: BLE001
        job.set_status(JobStatus.FAILED, f"Job crashed: {e}")
    finally:
        # Cleanup transcoded temps + uploaded originals
        for mp3 in mp3s:
            if mp3 not in files:
                mp3.unlink(missing_ok=True)
        for f in files:
            f.unlink(missing_ok=True)
        try:
            files[0].parent.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Static frontend (production build). Mounted LAST so /api/* wins routing.
# --------------------------------------------------------------------------- #

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
