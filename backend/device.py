"""
Walkman device detection — cross-platform.

The NW-S705F mounts as plain USB mass storage on all three OSes. The only
thing we have to do is find where the OS decided to mount it and confirm
it has an OMGAUDIO directory (Sony's database folder).
"""
from __future__ import annotations

import glob
import os
import platform
import shutil
from pathlib import Path


def _candidates() -> list[Path]:
    system = platform.system()
    if system == "Linux":
        user = os.environ.get("USER", "")
        return [
            Path(p)
            for p in glob.glob(f"/media/{user}/*") + glob.glob(f"/run/media/{user}/*")
        ]
    if system == "Darwin":
        return [Path(p) for p in glob.glob("/Volumes/*")]
    if system == "Windows":
        import string
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return []


def find_walkman() -> Path | None:
    """Return the mount point of the connected Walkman, or None."""
    # Dev/test escape hatch: MOCK_DEVICE_PATH replaces drive scanning entirely.
    # A mock device is any folder with an OMGAUDIO dir (empty is a valid
    # starting state — JSymphonic regenerates the full DB from nothing).
    mock = os.environ.get("MOCK_DEVICE_PATH")
    if mock:
        path = Path(mock)
        return path if (path / "OMGAUDIO").is_dir() else None
    for path in _candidates():
        if (path / "OMGAUDIO").is_dir():
            return path
    return None


def device_info(mount: Path) -> dict:
    """Disk usage + rough track count from the OMGAUDIO folder."""
    usage = shutil.disk_usage(mount)
    omg = mount / "OMGAUDIO"
    # Tracks live in numbered subfolders 10F00, 10F01... as .OMA files
    track_count = sum(1 for _ in omg.rglob("*.OMA")) if omg.is_dir() else 0
    return {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "track_count": track_count,
    }
