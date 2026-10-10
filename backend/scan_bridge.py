"""In-process handoff between media scans and the main-process elevation broker.

The HTTP routes that expose these methods are deliberately owned by main.py and
must require the native ``X-NightOps-Token`` header. Cookie authentication is
not sufficient for pending, claim, result, or cancel operations.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PENDING_PATH = "/api/internal/scanner/pending"
CLAIM_PATH = "/api/internal/scanner/claim"
RESULT_PATH = "/api/internal/scanner/result"
CANCEL_PATH = "/api/internal/scanner/cancel"
# Native exchange has a 30-minute batch deadline; retain admission through its exit.
ELEVATION_WAIT_SECONDS = 31 * 60


class BridgeError(RuntimeError):
    pass


@dataclass
class _Entry:
    request: dict[str, Any]
    created_at: float
    helper_pid: int | None = None
    result: dict[str, Any] | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    batch_id: str | None = None
    released: bool = True


class ElevationBridge:
    """Thread-safe, single-use elevation request registry."""

    def __init__(self, managed_roots: Iterable[Path], *, request_ttl_seconds: float = ELEVATION_WAIT_SECONDS,
                 result_ttl_seconds: float = 300,
                 trusted_status_max_age_seconds: float = 10) -> None:
        roots = tuple(Path(root).resolve(strict=True) for root in managed_roots)
        if not roots:
            raise ValueError("at least one managed root is required")
        self._roots = roots
        self._request_ttl = float(request_ttl_seconds)
        self._result_ttl = float(result_ttl_seconds)
        self._trusted_status_max_age = float(trusted_status_max_age_seconds)
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def request(self, media_id: str, path: Path, sha256: str, size_bytes: int, *, batch_id=None) -> dict[str, Any]:
        checked = self._checked_path(path)
        actual_hash, actual_size = _fingerprint(checked)
        if not hmac.compare_digest(actual_hash, str(sha256)) or actual_size != int(size_bytes):
            raise BridgeError("content changed before elevation request")
        now = time.time()
        request = {
            "request_id": str(uuid.uuid4()),
            "nonce": secrets.token_urlsafe(32),
            "media_id": str(media_id),
            "path": str(checked),
            "sha256": actual_hash,
            "size_bytes": actual_size,
            "created_at": now,
        }
        with self._lock:
            self._prune(now)
            self._entries[request["request_id"]] = _Entry(dict(request), now, batch_id=batch_id, released=batch_id is None)
        return dict(request)

    def release_batch(self, batch_id):
        with self._lock:
            now = time.time()
            for entry in self._entries.values():
                if entry.batch_id == batch_id and entry.result is None:
                    entry.released = True
                    entry.created_at = now

    def cancel_batch(self, batch_id):
        with self._lock:
            for entry in self._entries.values():
                if entry.batch_id == batch_id and entry.result is None:
                    self._fail(entry, 'batch_cancelled', 'Scan batch interrupted')

    def pending(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._expire_pending(now)
            return [dict(entry.request) for entry in self._entries.values()
                    if entry.result is None and entry.helper_pid is None and entry.released]

    def claim(self, request_id: str, nonce: str, helper_pid: int) -> dict[str, Any]:
        if int(helper_pid) <= 0:
            raise BridgeError("invalid helper process")
        with self._lock:
            entry = self._active(request_id)
            self._match(entry, "nonce", nonce)
            if entry.helper_pid is not None:
                raise BridgeError("request is already claimed")
            entry.helper_pid = int(helper_pid)
            return dict(entry.request)

    def submit(self, result: dict[str, Any]) -> dict[str, Any]:
        request_id = str(result.get("request_id") or "")
        with self._lock:
            entry = self._active(request_id)
            checked = self._checked_path(Path(entry.request["path"]))
            try:
                actual_hash, actual_size = _fingerprint(checked)
            except OSError as exc:
                self._fail(entry, "content_changed", f"content unavailable after elevation: {exc}")
                raise BridgeError("content changed during elevated scan") from exc
            if (not hmac.compare_digest(actual_hash, entry.request["sha256"])
                    or actual_size != entry.request["size_bytes"]):
                self._fail(entry, "content_changed", "content changed during elevated scan")
                raise BridgeError("content changed during elevated scan")
            if entry.helper_pid is None:
                raise BridgeError("request was not claimed by a helper")
            for key in ("nonce", "media_id", "path", "sha256", "size_bytes"):
                self._match(entry, key, result.get(key))
            if int(result.get("helper_pid") or 0) != entry.helper_pid:
                raise BridgeError("unexpected helper process")

            accepted = dict(result)
            accepted["accepted_at"] = time.time()
            accepted["policy_version"] = "defender-strict-v1"
            if accepted.get("ok"):
                required = {
                    "state": "CLEAN", "reason_code": "clean",
                    "defender_status": "clean", "exit_code": 0,
                }
                if any(accepted.get(key) != value for key, value in required.items()):
                    raise BridgeError("clean result is not an explicit Defender clearance")
                if not all(accepted.get(key) for key in (
                    "defender_engine_version", "defender_signature_version",
                    "defender_platform_version", "scanned_at",
                )):
                    raise BridgeError("clean result is missing Defender signature status")
            else:
                accepted["ok"] = False
                if accepted.get("state") == "CLEAN":
                    raise BridgeError("failed result cannot be clean")
            entry.result = accepted
            entry.ready.set()
            return dict(accepted)

    def wait(self, request_id: str, timeout: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(str(request_id))
            if entry is None:
                raise BridgeError("unknown elevation request")
            ready = entry.ready
        if not ready.wait(timeout):
            return None
        with self._lock:
            return dict(entry.result) if entry.result is not None else None

    def trusted_status(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return status only for the exact clean result authenticated this launch.

        Pass the returned value to ``scan.clearance_valid(..., trusted_status=value)``
        when unprivileged ``Get-MpComputerStatus`` is access denied. Results from a
        prior process launch cannot use this seam and must be rescanned.
        """
        request_id = str(record.get("request_id") or "")
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None or entry.result is None or not entry.result.get("ok"):
                raise BridgeError("record is not an authenticated clean helper result")
            accepted = entry.result
            if time.time() - float(accepted.get("accepted_at", 0)) > self._trusted_status_max_age:
                raise BridgeError("authenticated status is no longer immediate")
            for key in (
                "nonce", "media_id", "path", "sha256", "size_bytes", "helper_pid",
                "state", "reason_code", "defender_status", "exit_code", "scanned_at",
                "defender_engine_version", "defender_signature_version",
                "defender_platform_version", "policy_version",
            ):
                expected = accepted.get(key)
                supplied = record.get(key)
                if isinstance(expected, str) and isinstance(supplied, str):
                    matches = hmac.compare_digest(expected, supplied)
                else:
                    matches = expected == supplied
                if not matches:
                    raise BridgeError(f"clearance record {key} does not match authenticated result")
            return {
                "available": True,
                "active": True,
                "real_time_protection": True,
                "running_mode": "Normal",
                "engine_version": accepted["defender_engine_version"],
                "signature_version": accepted["defender_signature_version"],
                "platform_version": accepted["defender_platform_version"],
            }

    def cancel(self, request_id: str, reason_code: str = "elevation_cancelled") -> dict[str, Any]:
        with self._lock:
            entry = self._active(request_id)
            return dict(self._fail(entry, reason_code, "elevated Defender scan was cancelled"))

    def _checked_path(self, path: Path) -> Path:
        absolute = Path(os.path.abspath(path))
        if _has_reparse_component(absolute):
            raise BridgeError("reparse paths are not allowed")
        try:
            resolved = absolute.resolve(strict=True)
        except OSError as exc:
            raise BridgeError(f"managed file is unavailable: {exc}") from exc
        if not resolved.is_file() or not any(_is_beneath(resolved, root) for root in self._roots):
            raise BridgeError("path is outside managed storage")
        return resolved

    def _active(self, request_id: str) -> _Entry:
        entry = self._entries.get(str(request_id))
        if entry is None:
            raise BridgeError("unknown elevation request")
        if entry.result is not None:
            raise BridgeError("elevation request is already terminal")
        if time.time() - entry.created_at > self._request_ttl:
            self._fail(entry, "elevation_timeout", "elevated Defender scan expired")
            raise BridgeError("elevation request expired")
        return entry

    @staticmethod
    def _match(entry: _Entry, key: str, value: Any) -> None:
        expected = entry.request[key]
        if isinstance(expected, str) and isinstance(value, str):
            matches = hmac.compare_digest(expected, value)
        else:
            matches = expected == value
        if not matches:
            raise BridgeError(f"elevation result {key} does not match request")

    @staticmethod
    def _fail(entry: _Entry, reason_code: str, reason: str) -> dict[str, Any]:
        entry.result = {
            **entry.request, "ok": False, "state": "ERROR", "reason": reason,
            "reason_code": reason_code, "defender_status": "error",
            "helper_pid": entry.helper_pid or 0, "accepted_at": time.time(),
            "policy_version": "defender-strict-v1",
        }
        entry.ready.set()
        return entry.result

    def _expire_pending(self, now: float) -> None:
        for entry in self._entries.values():
            if entry.result is None and entry.released and now - entry.created_at > self._request_ttl:
                self._fail(entry, "elevation_timeout", "elevated Defender scan expired")

    def _prune(self, now: float) -> None:
        self._expire_pending(now)
        remove = [key for key, entry in self._entries.items()
                  if entry.result is not None and now - float(entry.result.get("accepted_at", now)) > self._result_ttl]
        for key in remove:
            del self._entries[key]


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_reparse_component(path: Path) -> bool:
    for component in [path, *path.parents]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
    return False


def _fingerprint(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk); size += len(chunk)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or size != after.st_size:
        raise OSError("file changed while hashing")
    return digest.hexdigest(), size
