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
import re
import threading
from pathlib import Path
from collections.abc import Mapping

from metadata import METADATA_LIMIT, TRANSFER_TAGS, normalize_metadata, parse_ffmetadata


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


def _run_bounded(cmd, *, timeout=600, output_limit=8 * 1024**2, stdout_limit=0):
    """Drain diagnostic output with a hard memory bound, including hostile tags."""
    chunks, output, overflow = [], [], threading.Event()
    with subprocess.Popen(cmd, stdout=subprocess.PIPE if stdout_limit else subprocess.DEVNULL,
                          stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        def read_pipe(pipe, sink, limit):
            count = 0
            while chunk := pipe.read(65536):
                count += len(chunk)
                if count > limit:
                    overflow.set()
                    process.kill()
                    return
                sink.append(chunk)
        readers = [threading.Thread(target=read_pipe,
                   args=(process.stderr, chunks, output_limit), daemon=True)]
        if stdout_limit:
            readers.append(threading.Thread(target=read_pipe,
                           args=(process.stdout, output, stdout_limit), daemon=True))
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            for reader in readers:
                reader.join()
    if overflow.is_set():
        raise AudioError('Audio diagnostics exceeded the processing limit')
    return subprocess.CompletedProcess(cmd, process.returncode,
        stdout=b''.join(output).decode('utf-8', 'replace') if stdout_limit else None,
        stderr=b''.join(chunks).decode('utf-8', 'replace'))


def convert_managed(src: Path, out: Path, kind: str, max_bytes: int,
                    metadata: Mapping | None = None) -> Path:
    """Called only while a cleared source lease is held by MediaService.

    No resampling or lossy codec in the playback compatibility derivative.
    Restrict demuxer protocols so metadata cannot cause a network fetch.
    """
    if kind not in ('playback', 'transfer'):
        raise AudioError('Unknown conversion purpose')
    options = (['-codec:a', 'flac'] if kind == 'playback' else
               ['-codec:a', 'libmp3lame', '-b:a', '192k', '-ar', '44100',
                '-ac', '2', '-id3v2_version', '3'])
    tags = []
    if metadata is not None:
        normalized = normalize_metadata(metadata, '')
        for key in TRANSFER_TAGS:
            # Explicit empty values remove malformed inherited numeric tags.
            tags.extend(['-metadata', f'{key}={normalized.get(key, "")}'])
    cmd = [_ensure_ffmpeg(), '-nostdin', '-v', 'error', '-y',
           '-protocol_whitelist', 'file,pipe', '-max_alloc', '67108864',
           '-i', str(src), '-map', '0:a:0', '-vn', '-map_metadata', '0',
           *tags, *options, '-fs', str(max_bytes), str(out)]
    try:
        result = _run_bounded(cmd)
        if result.returncode or not out.is_file() or out.stat().st_size == 0:
            raise AudioError('Audio conversion failed: ' + result.stderr[-1000:])
        if out.stat().st_size >= max_bytes - 65536:
            raise AudioError('Converted audio exceeds the managed artifact limit')
        return out
    except (OSError, subprocess.TimeoutExpired) as exc:
        out.unlink(missing_ok=True)
        raise AudioError('Audio converter was unavailable or timed out') from exc
    except BaseException:
        out.unlink(missing_ok=True)
        raise


def analyze_cleared_audio(src: Path) -> dict:
    """Decode only after clearance. Measure actual EBU R128 integrated loudness.

    Verbose per-frame filter logging stays disabled; stderr contains a bounded
    metadata/summary report, not one line per audio frame.
    """
    cmd = [_ensure_ffmpeg(), '-hide_banner', '-nostdin', '-nostats', '-v', 'info',
           '-protocol_whitelist', 'file,pipe', '-max_alloc', '67108864',
           '-i', str(src), '-map', '0:a:0', '-vn',
           '-af', 'ebur128=framelog=verbose:peak=true', '-f', 'null', '-',
           '-map_metadata', '0', '-f', 'ffmetadata', 'pipe:1']
    try:
        result = _run_bounded(cmd, stdout_limit=METADATA_LIMIT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioError('Audio analysis failed or timed out') from exc
    if result.returncode:
        raise AudioError('Audio format could not be decoded: ' + result.stderr[-700:])
    text = result.stderr
    try:
        result = parse_ffmetadata(result.stdout)
    except ValueError as exc:
        raise AudioError(str(exc)) from exc
    result['has_artwork'] = bool(re.search(r'^  Stream #0:\d+.*Video:.*\(attached pic\)',
                                          text, re.MULTILINE))
    duration = re.search(r'^  Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', text, re.MULTILINE)
    if duration:
        h, m, s = map(float, duration.groups())
        result['duration_seconds'] = h * 3600 + m * 60 + s
    loudness = re.findall(r'\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS', text)
    if loudness:
        measured = float(loudness[-1])
        result['integrated_lufs'] = measured if measured > -70 else None
    return result


def extract_cleared_artwork(src: Path, out: Path, max_bytes: int = 1024 * 1024) -> Path | None:
    """Extract one embedded cover from a cleared source into a bounded JPEG.

    The caller holds the source lease and must scan this new derivative before
    exposing it. No picture is a normal None result; other failures raise and
    remove any partial derivative, so the caller can mark optional art failed.
    """
    if src.resolve() == out.resolve():
        raise AudioError('Artwork output must not overwrite its source')
    if max_bytes < 1024 or max_bytes > 1024 * 1024:
        raise AudioError('Artwork size limit must be between 1 KiB and 1 MiB')
    cmd = [_ensure_ffmpeg(), '-hide_banner', '-nostdin', '-nostats', '-v', 'error', '-y',
           '-protocol_whitelist', 'file,pipe', '-max_alloc', '67108864', '-threads', '1',
           '-max_pixels', '16777216', '-filter_threads', '1',
           '-i', str(src), '-map', '0:v:disp:attached_pic:0', '-an', '-sn', '-dn',
           '-map_metadata', '-1', '-map_chapters', '-1', '-frames:v', '1',
           '-vf', "scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease,setsar=1",
           '-threads', '1', '-c:v', 'mjpeg', '-q:v', '3', '-pix_fmt', 'yuvj420p',
           '-f', 'image2', '-update', '1', '-fs', str(max_bytes), str(out)]
    try:
        result = _run_bounded(cmd, timeout=30, output_limit=128 * 1024)
        if result.returncode:
            if (re.search(r"^Stream map '[^'\r\n]*' matches no streams\.\r?$", result.stderr, re.MULTILINE)
                    and "Failed to set value '0:v:disp:attached_pic:0' for option 'map'" in result.stderr):
                out.unlink(missing_ok=True)
                return None
            raise AudioError('Embedded artwork could not be decoded')
        if not out.is_file() or not 0 < out.stat().st_size < max_bytes:
            raise AudioError('Embedded artwork exceeds the processing limit')
        # Generated content, not a copied attachment: JPEG only, no inherited tags.
        with out.open('rb') as image:
            start = image.read(2)
            image.seek(-2, os.SEEK_END)
            end = image.read(2)
        if start != b'\xff\xd8' or end != b'\xff\xd9':
            raise AudioError('Embedded artwork derivative is not a complete JPEG')
        return out
    except (OSError, subprocess.TimeoutExpired) as exc:
        out.unlink(missing_ok=True)
        raise AudioError('Artwork converter was unavailable or timed out') from exc
    except BaseException:
        out.unlink(missing_ok=True)
        raise
