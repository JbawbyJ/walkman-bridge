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


app = FastAPI(title="Walkman Bridge", version="0.1.1")

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
    we refetch only when invalid (first call / after a mutation), when the
    mount changes, or on an explicit ?refresh=1.

    Locking: the state lock protects fields and is only ever held briefly —
    never across a fetch. A refresh can take minutes when a transfer holds the
    shim lock, so exactly one caller performs it (the refresh lock) while
    everyone else is served the last-known list immediately. That keeps the
    dashboard's 3s polling responsive during long transfers, and it makes
    invalidate() safe to call from the event loop.
    """

    def __init__(self) -> None:
        self._state = threading.Lock()
        self._refresh = threading.Lock()
        self._mount: Path | None = None
        self._tracks: list[dict] = []
        self._valid = False

    def get(self, mount: Path, refresh: bool = False) -> list[dict]:
        with self._state:
            if self._valid and not refresh and self._mount == mount:
                return list(self._tracks)
            stale = list(self._tracks) if self._mount == mount else None

        if self._refresh.acquire(blocking=False):
            try:
                tracks = list_tracks(mount)  # may block behind the shim lock
                with self._state:
                    self._tracks = tracks
                    self._mount = mount
                    self._valid = True
                return list(tracks)
            finally:
                self._refresh.release()

        # Another thread is refreshing: serve what we have rather than parking
        # a second executor thread behind a minutes-long shim run.
        if stale is not None:
            return stale
        # Nothing usable for this mount — wait for the in-flight refresh once.
        with self._refresh:
            pass
        with self._state:
            if self._valid and self._mount == mount:
                return list(self._tracks)
        raise JSymphonicError("track list is unavailable (refresh failed)")

    def invalidate(self) -> None:
        # Cheap flag flip: keeps the last-known list to serve while the next
        # poll triggers the actual re-read.
        with self._state:
            self._valid = False

    def peek_count(self, mount: Path) -> int | None:
        """Track count if the cache is fresh for this mount, else None."""
        with self._state:
            if self._valid and self._mount == mount:
                return len(self._tracks)
            return None


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
# Upload filename hygiene
# --------------------------------------------------------------------------- #

_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_upload_name(raw: str | None, idx: int) -> str:
    """Client filenames are untrusted: strip any directory part (both slash
    flavors), and refuse empties and Windows reserved device names."""
    name = Path((raw or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return f"upload-{idx}"
    if name.split(".")[0].lower() in _RESERVED_NAMES:
        return f"upload-{idx}-{name}"
    return name


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/device", response_model=DeviceResponse)
async def get_device():
    # Drive scanning + rglob are disk I/O — keep them off the event loop.
    mount = await asyncio.to_thread(find_walkman)
    if not mount:
        return DeviceResponse(connected=False)
    info = await asyncio.to_thread(device_info, mount)
    # Prefer the DB-backed count when we have it, so the device panel and the
    # track list can't disagree.
    cached = track_cache.peek_count(mount)
    if cached is not None:
        info["track_count"] = cached
    return DeviceResponse(connected=True, mount_path=str(mount), **info)


@app.get("/api/tracks", response_model=List[TrackResponse])
async def get_tracks(refresh: int = 0):
    mount = await asyncio.to_thread(find_walkman)
    if not mount:
        raise HTTPException(status_code=404, detail="No Walkman detected")
    try:
        tracks = await asyncio.to_thread(track_cache.get, mount, bool(refresh))
    except JSymphonicError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return tracks


@app.delete("/api/tracks/{track_id}")
async def delete_track(track_id: str):
    mount = await asyncio.to_thread(find_walkman)
    if not mount:
        raise HTTPException(status_code=404, detail="No Walkman detected")
    try:
        await asyncio.to_thread(remove_track, mount, track_id)
    except JSymphonicError as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Even a failed delete may have touched the DB — force a re-read.
        track_cache.invalidate()
    return {"ok": True}


@app.post("/api/upload")
async def upload(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
):
    mount = await asyncio.to_thread(find_walkman)
    if not mount:
        raise HTTPException(status_code=404, detail="No Walkman detected")

    job_id = str(uuid.uuid4())
    job_dir = WORK_DIR / job_id

    # Persist uploads before kicking off the background task — the UploadFile
    # handles close when the request ends. Runs in a thread (disk copy of the
    # whole batch), and a half-written batch is removed rather than leaked.
    def persist() -> list[Path]:
        job_dir.mkdir()
        saved: list[Path] = []
        try:
            for idx, f in enumerate(files):
                target = job_dir / _safe_upload_name(f.filename, idx)
                if target.exists():  # duplicate names within one batch
                    target = job_dir / f"{idx}-{target.name}"
                with target.open("wb") as out:
                    shutil.copyfileobj(f.file, out)
                saved.append(target)
            return saved
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

    try:
        saved = await asyncio.to_thread(persist)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not store upload: {e}")

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

def _shim_event_to_job(job: Job, total_files: int) -> Callable[[dict], None]:
    """Map shim protocol events into the job's log/progress/message fields.

    The shim reports percent PER FILE and also emits file/progress events for
    the database-update step — only transfer-step progress moves the bar, and
    it is scaled across the batch.
    """
    state = {"started": 0}

    def handle(event: dict) -> None:
        kind = event.get("event")
        step = event.get("step")
        if kind == "file":
            name = event.get("name") or "?"
            if step == "transfer":
                state["started"] += 1
                job.message = f"Transferring {name}"
                job.log(f"  > {name}")
            elif step == "update":
                job.message = "Updating device database"
                job.log(f"  db > {name}")
            else:
                job.log(f"  {step} > {name}")
        elif kind == "progress":
            percent = event.get("percent")
            if step == "transfer" and isinstance(percent, (int, float)) and total_files:
                done = max(0, state["started"] - 1)
                pct = min(max(float(percent), 0.0), 100.0) / 100.0
                frac = min((done + pct) / total_files, 1.0)
                job.progress = 0.5 + 0.5 * frac
        elif kind == "step":
            error = event.get("error")
            suffix = f"  ! {error}" if error else ""
            job.log(f"  {step}: {event.get('state')}{suffix}")
        elif kind == "fatal":
            job.log(f"  ! {event.get('message')}")

    return handle


async def process_upload_job(job_id: str, files: List[Path], mount: Path):
    job = job_store.get(job_id)
    assert job is not None
    job.set_status(JobStatus.RUNNING, "Starting transfer")

    mp3s: list[Path] = []
    touched_device = False
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
        touched_device = True
        try:
            await asyncio.to_thread(
                add_tracks, mount, mp3s, _shim_event_to_job(job, len(mp3s))
            )
        except JSymphonicError as e:
            job.set_status(JobStatus.FAILED, f"Transfer failed: {e}")
            return

        job.progress = 1.0
        job.set_status(JobStatus.DONE, "All files processed")
    except Exception as e:  # noqa: BLE001
        job.set_status(JobStatus.FAILED, f"Job crashed: {e}")
    finally:
        if touched_device:
            # Success or failure: the device DB may have changed either way.
            track_cache.invalidate()
        # Cleanup transcoded temps + uploaded originals. One stubborn file
        # (antivirus scan, etc.) must not abort the rest of the cleanup.
        for mp3 in mp3s:
            if mp3 not in files:
                try:
                    mp3.unlink(missing_ok=True)
                except OSError as e:
                    job.log(f"  ! could not remove temp {mp3.name}: {e}")
        for f in files:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
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
