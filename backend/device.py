"""
Walkman device detection — cross-platform.

The NW-S705F mounts as plain USB mass storage on all three OSes. The only
thing we have to do is find where the OS decided to mount it and confirm
it has an OMGAUDIO directory (Sony's database folder).
"""
from __future__ import annotations

import glob
import hashlib
import os
import platform
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4


class BackupError(Exception):
    """Backup refused or failed without writing the Walkman."""


class DeviceChangedError(RuntimeError):
    """The admitted mount disappeared or now belongs to a different volume."""

    def __init__(self, message, *, needs_reconcile=False):
        super().__init__(message)
        self.needs_reconcile = needs_reconcile


@dataclass(frozen=True)
class DeviceIdentity:
    mount: Path
    volume_id: str


def _volume_identity(mount: Path) -> str:
    """Bind a mount to its OS volume identity, not just its drive letter.

    The directory inode also distinguishes temporary mock mounts on one disk.
    Unlike timestamps, it does not change when OMGAUDIO is rewritten.
    """
    info = mount.stat()
    directory = f"{info.st_dev}:{info.st_ino}"
    if os.name != "nt":
        return directory
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    volume_root = ctypes.create_unicode_buffer(32768)
    if not kernel.GetVolumePathNameW(str(mount), volume_root, len(volume_root)):
        raise ctypes.WinError(ctypes.get_last_error())
    volume_name = ctypes.create_unicode_buffer(1024)
    if not kernel.GetVolumeNameForVolumeMountPointW(volume_root.value, volume_name, len(volume_name)):
        raise ctypes.WinError(ctypes.get_last_error())
    serial = wintypes.DWORD()
    if not kernel.GetVolumeInformationW(volume_root.value, None, 0, ctypes.byref(serial), None, None, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return f"{volume_name.value}:{serial.value:08x}:{directory}"


def capture_device_identity(mount: Path) -> DeviceIdentity:
    mount = Path(mount).absolute()
    try:
        if not mount.is_dir() or not (mount / "OMGAUDIO").is_dir():
            raise DeviceChangedError("Walkman is no longer connected")
        return DeviceIdentity(mount, _volume_identity(mount))
    except OSError as exc:
        raise DeviceChangedError(f"Cannot identify the Walkman volume: {exc}") from exc


def revalidate_device(identity: DeviceIdentity) -> Path:
    current = capture_device_identity(identity.mount)
    if current.volume_id != identity.volume_id:
        raise DeviceChangedError("The Walkman volume changed; reconnect and refresh before retrying")
    return identity.mount


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
        import ctypes
        import string

        # Removable drives first: the Walkman is USB mass storage, and a
        # drive-root backup of it on a fixed disk must not win the scan.
        # A:/B: are legacy floppy letters — probing them can stall.
        DRIVE_REMOVABLE = 2
        removable: list[Path] = []
        fixed: list[Path] = []
        for d in string.ascii_uppercase[2:]:
            root = f"{d}:\\"
            if not Path(root).exists():
                continue
            kind = ctypes.windll.kernel32.GetDriveTypeW(root)
            (removable if kind == DRIVE_REMOVABLE else fixed).append(Path(root))
        return removable + fixed
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


def backup_dir() -> Path:
    """Local folder that holds dated device copies. Never on the Walkman."""
    override = os.environ.get("WALKMAN_BACKUP_DIR")
    if override:
        return Path(override)
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return Path(local_app) / "Walkman Bridge" / "backups"
    return Path.home() / ".local" / "share" / "walkman-bridge" / "backups"


def _tree_manifest(root: Path) -> dict[str, tuple]:
    manifest = {}

    def unreadable(error):
        raise error

    for directory, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in directories + files:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise BackupError("Backup refuses symbolic links and reparse points")
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                manifest[relative] = ("directory",)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                manifest[relative] = ("file", info.st_size, digest.hexdigest())
            else:
                raise BackupError("Backup refuses unsupported filesystem entries")
    return manifest


def _reject_copy_links(directory, names):
    """Recheck links at copy time, including Windows junctions copytree follows."""
    for path in [Path(directory), *(Path(directory) / name for name in names)]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BackupError("Backup refuses symbolic links and reparse points")
    return []


def backup_device(mount: Path, dest_root: Path | None = None) -> Path:
    """Copy the mount (including OMGAUDIO) to a dated local folder.

    Reads `mount`; writes only under `dest_root`. Raises BackupError if the
    destination would land on the Walkman.
    """
    mount = Path(mount).absolute()
    if not mount.is_dir():
        raise BackupError(f"mount is not a directory: {mount}")
    dest_root = Path(dest_root) if dest_root is not None else backup_dir()

    # Refuse before mkdir — a dest_root on the device must not be created.
    try:
        dest_root.resolve().relative_to(mount.resolve())
    except ValueError:
        pass
    else:
        raise BackupError("backup destination must not be on the Walkman")

    try:
        identity = capture_device_identity(mount)
        before = _tree_manifest(mount)
        dest_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        partial = dest_root / f".{stamp}-{uuid4().hex}.partial"
        # A failed copy stays visibly partial; only a verified tree is published.
        shutil.copytree(mount, partial, symlinks=True, ignore=_reject_copy_links)
        revalidate_device(identity)
        if _tree_manifest(partial) != before or _tree_manifest(mount) != before:
            raise BackupError("Backup verification failed: the source or copied files changed")
        revalidate_device(identity)
        dest = dest_root / stamp
        n = 1
        while dest.exists():
            dest = dest_root / f"{stamp}-{n}"
            n += 1
        partial.rename(dest)
        return dest
    except (OSError, shutil.Error, DeviceChangedError) as exc:
        raise BackupError(f"Backup failed: {exc}") from exc


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
