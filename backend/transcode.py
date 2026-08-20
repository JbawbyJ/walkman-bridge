"""
Audio normalization.

The NW-S705F accepts MP3 at 32-320 kbps, 44.1 kHz. We normalize every input
through ffmpeg to a safe MP3 profile (192 kbps CBR, 44.1 kHz, stereo) and
preserve ID3 tags so JSymphonic can read title/artist/album.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class AudioError(RuntimeError):
    pass


# Inputs that are already compatible can be passed straight through if you
# trust the source. For simplicity v1 always re-encodes — it's slow but safe.
SAFE_PASSTHROUGH = False


def _ensure_ffmpeg() -> str:
    # The installed app ships its own ffmpeg and points here; a dev checkout
    # falls back to PATH. Mirrors WALKMAN_BRIDGE_JAVA in jsymphonic.py.
    bundled = os.environ.get("WALKMAN_BRIDGE_FFMPEG")
    if bundled:
        if not Path(bundled).is_file():
            raise AudioError(
                f"WALKMAN_BRIDGE_FFMPEG points at {bundled}, which does not exist"
            )
        return bundled

    path = shutil.which("ffmpeg")
    if not path:
        raise AudioError("ffmpeg not found in PATH — install it first")
    return path


def _closed_temp_mp3() -> Path:
    """Create an empty temp MP3 and close the handle before ffmpeg opens it.

    mkstemp leaves the file open. On Windows that exclusive handle blocks
    ffmpeg's `-y` overwrite (and leaks a descriptor per transfer). Close it
    immediately — same rule as the rest of the Windows file-locking fixes.
    """
    fd, name = tempfile.mkstemp(suffix=".mp3", prefix="wbridge-")
    os.close(fd)
    return Path(name)


def normalize_to_mp3(src: Path) -> Path:
    """Convert any audio file to a Walkman-friendly MP3 in a temp dir."""
    ffmpeg = _ensure_ffmpeg()
    if not src.exists():
        raise AudioError(f"Source not found: {src}")

    if SAFE_PASSTHROUGH and src.suffix.lower() == ".mp3":
        return src

    out = _closed_temp_mp3()

    cmd = [
        ffmpeg,
        "-y",                      # overwrite the tempfile we just created
        "-i", str(src),
        "-vn",                     # drop any video/album-art stream issues
        "-map_metadata", "0",      # carry tags through
        "-id3v2_version", "3",     # ID3v2.3 — best Walkman compatibility
        "-codec:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        str(out),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            # Windows default is cp1252 — non-ASCII metadata in ffmpeg's stderr
            # must not blow up the decode. No console window from the frozen app.
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as e:
        out.unlink(missing_ok=True)
        raise AudioError(f"ffmpeg timed out on {src.name}") from e

    if result.returncode != 0:
        out.unlink(missing_ok=True)
        # ffmpeg writes the useful error to stderr's tail
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        raise AudioError(f"ffmpeg failed: {tail}")

    return out
