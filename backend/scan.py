"""Fail-closed audio format and Microsoft Defender clearance gate."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ENGINE = "audio-guard"
POLICY_VERSION = "defender-strict-v1"
CLEARANCE_SECONDS = 24 * 60 * 60
MAX_BYTES = 500 * 1024 * 1024
MAX_TAG_BYTES = 2 * 1024 * 1024
DEFENDER_TIMEOUT_SECONDS = 120

STATES = ("SCANNING", "CLEAN", "BLOCKED", "AWAITING_PERMISSION", "ERROR", "TRANSCODE", "WRITING", "THREAT")
_EXT_FAMILY = {
    ".mp3": "mpeg", ".mp2": "mpeg", ".mpga": "mpeg", ".flac": "flac",
    ".ogg": "ogg", ".oga": "ogg", ".opus": "ogg", ".wav": "wav",
    ".m4a": "mp4", ".m4b": "mp4", ".m4p": "mp4", ".mp4": "mp4", ".aac": "aac",
}
_MP4_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"mp41", b"mp42", b"isom", b"iso2", b"mp71"}
_DENIED = re.compile(r"access\s+is\s+denied|access\s+denied|requires?\s+elevation|error\s*740|0x80070005", re.I)
_THREAT = re.compile(r"\bthreat(?:s)?\b.*\b(?:found|detected)\b|\bfound\s+[1-9]\d*\s+threat", re.I | re.S)


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanResult:
    ok: bool
    state: str
    reason: str
    engine: str = ENGINE
    pid: str = ""
    reason_code: str = "scan_error"
    defender_status: str = "unknown"
    sha256: str = ""
    size_bytes: int = 0
    policy_version: str = POLICY_VERSION
    scanned_at: float = 0.0
    defender_engine_version: str = ""
    defender_signature_version: str = ""
    defender_platform_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_lock = threading.Lock()
_rows: deque[dict[str, Any]] = deque(maxlen=50)
_counters = {"cleared": 0, "blocked": 0}
_seq = 0


def _utc_timestamp() -> float:
    return time.time()


def reset_for_tests() -> None:
    global _seq
    with _lock:
        _rows.clear()
        _counters.update(cleared=0, blocked=0)
        _seq = 0


def begin(target: str, proc: str = ENGINE) -> str:
    global _seq
    target_path = Path(target)
    parent = target_path.parent.name.lower()
    media_id = parent if re.fullmatch(r"[0-9a-f]{32}", parent) else ""
    with _lock:
        _seq += 1
        pid = f"ag-{_seq}"
        _rows.append({"pid": pid, "proc": proc, "target": target_path.name,
                      "media_id": media_id, "state": "SCANNING", "reason_code": "scanning"})
        return pid


def update(pid: str, state: str, reason_code: str | None = None) -> None:
    if state not in STATES:
        raise ScanError(f"invalid scan state: {state}")
    with _lock:
        for row in reversed(_rows):
            if row["pid"] != pid:
                continue
            previous = row["state"]
            row["state"] = state
            if reason_code:
                row["reason_code"] = reason_code
            if previous == "SCANNING":
                if state == "CLEAN":
                    _counters["cleared"] += 1
                elif state in {"BLOCKED", "THREAT"}:
                    _counters["blocked"] += 1
            return


def snapshot() -> dict[str, Any]:
    capability = _query_defender_status()
    with _lock:
        rows = [dict(row) for row in _rows]
        counters = dict(_counters)
    return {
        "engine": ENGINE,
        "threats": counters["blocked"],
        **counters,
        "capability": capability,
        "rows": rows,
    }


def scan_audio(path: Path) -> ScanResult:
    """Validate an audio container and require an explicit clean Defender result."""
    path = Path(os.path.abspath(path))
    pid = begin(str(path))
    result = _scan(path, pid)
    update(pid, result.state, result.reason_code)
    return result


def scan_artwork(path: Path) -> ScanResult:
    """Narrow generated-JPEG gate; never makes an image valid as audio."""
    path = Path(os.path.abspath(path))
    pid = begin(str(path))
    result = _scan(path, pid, artwork=True)
    update(pid, result.state, result.reason_code)
    return result


def _result(pid: str, *, ok: bool, state: str, reason: str, reason_code: str,
            defender_status: str = "not_scanned", fingerprint: tuple[str, int] = ("", 0),
            status: dict[str, Any] | None = None) -> ScanResult:
    status = status or {}
    return ScanResult(
        ok=ok, state=state, reason=reason, pid=pid, reason_code=reason_code,
        defender_status=defender_status, sha256=fingerprint[0], size_bytes=fingerprint[1],
        scanned_at=_utc_timestamp(),
        defender_engine_version=str(status.get("engine_version") or ""),
        defender_signature_version=str(status.get("signature_version") or ""),
        defender_platform_version=str(status.get("platform_version") or ""),
    )


def _scan(path: Path, pid: str, *, artwork=False) -> ScanResult:
    try:
        if _has_reparse_component(path):
            return _result(pid, ok=False, state="BLOCKED", reason="reparse paths are not scannable", reason_code="reparse_path")
        fingerprint = _fingerprint(path)
    except (OSError, ValueError) as exc:
        return _result(pid, ok=False, state="ERROR", reason=f"unreadable: {exc}", reason_code="unreadable")
    if fingerprint[1] <= 0 or fingerprint[1] > MAX_BYTES:
        return _result(pid, ok=False, state="BLOCKED", reason="file size is outside policy", reason_code="invalid_size", fingerprint=fingerprint)

    format_error = _validate_artwork_format(path, fingerprint[1]) if artwork else _validate_audio_format(path, fingerprint[1])
    if format_error:
        return _result(pid, ok=False, state="BLOCKED", reason=format_error,
                       reason_code="invalid_artwork_format" if artwork else "invalid_audio_format", fingerprint=fingerprint)

    try:
        invocation = _invoke_defender(path)
    except subprocess.TimeoutExpired:
        return _result(pid, ok=False, state="ERROR", reason="Microsoft Defender scan timed out", reason_code="defender_timeout", defender_status="error", fingerprint=fingerprint)
    except OSError as exc:
        return _result(pid, ok=False, state="ERROR", reason=f"Microsoft Defender scan failed: {exc}", reason_code="defender_scan_error", defender_status="error", fingerprint=fingerprint)
    if invocation is None:
        return _result(pid, ok=False, state="ERROR", reason="Microsoft Defender is unavailable", reason_code="defender_unavailable", defender_status="unavailable", fingerprint=fingerprint)

    blob = f"{invocation.stdout or ''}\n{invocation.stderr or ''}"
    if _THREAT.search(blob):
        return _result(pid, ok=False, state="BLOCKED", reason="Microsoft Defender reported a threat", reason_code="defender_threat", defender_status="threat", fingerprint=fingerprint)
    if _DENIED.search(blob) or invocation.returncode in {5, 740}:
        return _result(pid, ok=False, state="AWAITING_PERMISSION", reason="Microsoft Defender requires elevation for this file", reason_code="defender_elevation_required", defender_status="elevation_required", fingerprint=fingerprint)
    if invocation.returncode != 0:
        return _result(pid, ok=False, state="ERROR", reason=f"Microsoft Defender exited with code {invocation.returncode}", reason_code="defender_scan_error", defender_status="error", fingerprint=fingerprint)

    try:
        after = _fingerprint(path)
    except OSError:
        after = ("", -1)
    if after != fingerprint:
        return _result(pid, ok=False, state="ERROR", reason="file content changed during scan", reason_code="content_changed", defender_status="error", fingerprint=after)

    status = _query_defender_status()
    if not _status_complete(status):
        if status.get("requires_elevation"):
            return _result(pid, ok=False, state="AWAITING_PERMISSION",
                           reason="Microsoft Defender status requires elevation",
                           reason_code="defender_elevation_required",
                           defender_status="elevation_required", fingerprint=fingerprint,
                           status=status)
        return _result(pid, ok=False, state="ERROR", reason="Microsoft Defender signature status is unavailable", reason_code="defender_status_unavailable", defender_status="unavailable", fingerprint=fingerprint, status=status)
    return _result(pid, ok=True, state="CLEAN", reason="Microsoft Defender reported clean", reason_code="clean", defender_status="clean", fingerprint=fingerprint, status=status)


def clearance_valid(path: Path, record: dict[str, Any], *,
                    trusted_status: dict[str, Any] | None = None) -> bool:
    """Validate a 24-hour clearance against content, policy, and live signatures."""
    try:
        if not record.get("ok") or record.get("state") != "CLEAN" or record.get("defender_status") != "clean":
            return False
        if record.get("policy_version") != POLICY_VERSION:
            return False
        age = _utc_timestamp() - float(record.get("scanned_at", 0))
        if age < 0 or age > CLEARANCE_SECONDS:
            return False
        digest, size = _fingerprint(Path(path))
        if digest != record.get("sha256") or size != int(record.get("size_bytes", -1)):
            return False
        status = trusted_status if trusted_status is not None else _query_defender_status()
        if not _status_complete(status):
            return False
        return all(
            str(record.get(record_key) or "") == str(status.get(status_key) or "")
            for record_key, status_key in (
                ("defender_engine_version", "engine_version"),
                ("defender_signature_version", "signature_version"),
                ("defender_platform_version", "platform_version"),
            )
        )
    except (OSError, TypeError, ValueError):
        return False


def _fingerprint(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or size != after.st_size:
        raise OSError("file changed while hashing")
    return digest.hexdigest(), size


def _has_reparse_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    for component in [absolute, *absolute.parents]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
    return False


def _synchsafe(raw: bytes) -> int:
    if len(raw) != 4 or any(byte & 0x80 for byte in raw):
        raise ValueError("invalid synchsafe integer")
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def _validate_audio_format(path: Path, size: int) -> str | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(10)
            offset = 0
            if header.startswith(b"ID3"):
                if len(header) != 10 or header[3] not in (2, 3, 4):
                    return "invalid ID3 header"
                tag_size = _synchsafe(header[6:10])
                footer = 10 if header[5] & 0x10 else 0
                total = 10 + tag_size + footer
                if tag_size > MAX_TAG_BYTES or total >= size:
                    return "truncated or oversized ID3 tag"
                stream.seek(0)
                tag = stream.read(total)
                if len(tag) != total or _validate_id3(tag):
                    return "malformed ID3 frames"
                offset = total
            stream.seek(offset)
            body = stream.read(64)
    except (OSError, ValueError) as exc:
        return f"audio header is unreadable: {exc}"
    kind = _detect_kind(body)
    family = _EXT_FAMILY.get(path.suffix.lower())
    if not kind or not family or not (family == kind or family == "aac" and kind in {"aac", "mp4"}):
        return "extension and audio container do not match"
    return None


def _validate_artwork_format(path: Path, size: int) -> str | None:
    if path.name != 'artwork.jpg' or not 5 <= size <= 1024 * 1024:
        return 'Artwork must be a managed JPEG no larger than 1 MiB'
    try:
        with path.open('rb') as stream:
            start = stream.read(3)
            stream.seek(-2, 2)
            end = stream.read(2)
    except OSError:
        return 'Artwork could not be read'
    if start != b'\xff\xd8\xff' or end != b'\xff\xd9':
        return 'Artwork JPEG signature is invalid'
    return None


def _validate_id3(tag: bytes) -> bool:
    version = tag[3]
    body = tag[10:-10] if tag[5] & 0x10 else tag[10:]
    if tag[5] & 0x40:
        if len(body) < 4:
            return True
        try:
            ext_size = _synchsafe(body[:4]) if version == 4 else int.from_bytes(body[:4], "big") + 4
        except ValueError:
            return True
        if ext_size < 4 or ext_size > len(body):
            return True
        body = body[ext_size:]
    header_size = 6 if version == 2 else 10
    id_size = 3 if version == 2 else 4
    while body:
        if all(byte == 0 for byte in body):
            return False
        if len(body) < header_size:
            return True
        frame_id = body[:id_size]
        if not all(48 <= byte <= 57 or 65 <= byte <= 90 for byte in frame_id):
            return True
        try:
            frame_size = int.from_bytes(body[3:6], "big") if version == 2 else (_synchsafe(body[4:8]) if version == 4 else int.from_bytes(body[4:8], "big"))
        except ValueError:
            return True
        if frame_size <= 0 or header_size + frame_size > len(body):
            return True
        payload = body[header_size:header_size + frame_size]
        if frame_id in {b"APIC", b"PIC"} and not _valid_apic(payload, version):
            return True
        body = body[header_size + frame_size:]
    return False


def _find_terminator(payload: bytes, start: int, encoding: int) -> int:
    if encoding in (0, 3):
        return payload.find(b"\x00", start)
    for index in range(start, len(payload) - 1, 2):
        if payload[index:index + 2] == b"\x00\x00":
            return index
    return -1


def _valid_apic(payload: bytes, version: int) -> bool:
    if len(payload) < 6 or payload[0] not in (0, 1, 2, 3):
        return False
    encoding = payload[0]
    if version == 2:
        cursor = 4
    else:
        mime_end = payload.find(b"\x00", 1)
        if mime_end <= 1:
            return False
        try:
            mime = payload[1:mime_end].decode("ascii").lower()
        except UnicodeDecodeError:
            return False
        if not (mime.startswith("image/") or mime == "-->"):
            return False
        cursor = mime_end + 1
    if cursor >= len(payload):
        return False
    cursor += 1  # picture type
    description_end = _find_terminator(payload, cursor, encoding)
    if description_end < 0:
        return False
    image_start = description_end + (1 if encoding in (0, 3) else 2)
    return image_start < len(payload)


def _detect_kind(data: bytes) -> str | None:
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"OggS"):
        return "ogg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 12 and data[4:8] == b"ftyp" and any(data[i:i + 4] in _MP4_BRANDS for i in range(8, min(len(data) - 3, 32), 4)):
        return "mp4"
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        return "aac" if (data[1] >> 1) & 0x03 == 0 else "mpeg"
    return None


def _defender_exe() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe",
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows Defender" / "Platform",
    ]
    if candidates[0].is_file():
        return candidates[0]
    platform = candidates[1]
    if platform.is_dir():
        for folder in sorted(platform.iterdir(), reverse=True):
            executable = folder / "MpCmdRun.exe"
            if executable.is_file():
                return executable
    found = shutil.which("MpCmdRun.exe")
    return Path(found) if found else None


def _invoke_defender(path: Path) -> subprocess.CompletedProcess[str] | None:
    executable = _defender_exe()
    if executable is None:
        return None
    return subprocess.run(
        [str(executable), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=DEFENDER_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )


def _query_defender_status() -> dict[str, Any]:
    registry = _query_defender_registry_status()
    if registry.get('source') == 'windows_security_center' and registry.get('available'):
        return registry
    if os.name != 'nt':
        return {"available": False}
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)):
        return {'available': False}
    system = Path(buffer.value)
    powershell = system / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    module = system / 'WindowsPowerShell' / 'v1.0' / 'Modules' / 'Defender' / 'Defender.psd1'
    utility = system / 'WindowsPowerShell' / 'v1.0' / 'Modules' / 'Microsoft.PowerShell.Utility' / 'Microsoft.PowerShell.Utility.psd1'
    if not powershell.is_file() or not module.is_file():
        return {'available': False}
    script = (
        "$PSModuleAutoLoadingPreference='None';"
        "Import-Module -Name '" + str(utility).replace("'", "''") + "' -ErrorAction Stop;"
        "Import-Module -Name '" + str(module).replace("'", "''") + "' -ErrorAction Stop;"
        "$s=Defender\\Get-MpComputerStatus -ErrorAction Stop;"
        "@{available=$true;active=$s.AntivirusEnabled;"
        "real_time_protection=$s.RealTimeProtectionEnabled;running_mode=$s.AMRunningMode;"
        "engine_version=$s.AMEngineVersion;"
        "signature_version=$s.AntivirusSignatureVersion;"
        "platform_version=$s.AMProductVersion}|Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            cwd=str(system), env={key: value for key, value in os.environ.items()
                if not key.upper().startswith(('DOTNET_', 'COMPLUS_', 'CORECLR_', 'COR_', 'PSMODULEPATH'))},
        )
        if result.returncode != 0:
            blob = f"{result.stdout or ''}\n{result.stderr or ''}"
            return {"available": False, "requires_elevation": bool(_DENIED.search(blob))}
        parsed = json.loads(result.stdout)
        return parsed if isinstance(parsed, dict) else {"available": False}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"available": False}


def _query_defender_registry_status() -> dict[str, Any]:
    """Read the same live Defender versions without requiring Get-MpComputerStatus.

    The HKLM signature values and Defender platform directory are readable by a
    standard Windows user. Service state is checked separately so stale registry
    values cannot claim that a disabled/stopped engine is available.
    """
    if os.name != "nt":
        return {"available": False}
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows Defender\Signature Updates",
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            signature = str(winreg.QueryValueEx(key, "AVSignatureVersion")[0])
            engine = str(winreg.QueryValueEx(key, "EngineVersion")[0])
        if not _defender_policy_active(winreg):
            return {"available": False, "disabled": True}
        platform_root = (
            Path(os.environ.get("ProgramData", r"C:\ProgramData"))
            / "Microsoft" / "Windows Defender" / "Platform"
        )
        platform_dirs = sorted(
            (folder.name for folder in platform_root.iterdir()
             if folder.is_dir() and (folder / "MpCmdRun.exe").is_file()),
            key=_version_key,
            reverse=True,
        )
        if not platform_dirs:
            return {"available": False}
        platform = re.sub(r"-\d+$", "", platform_dirs[0])
        if not _defender_service_running():
            return {"available": False}
        from defender_status import query_defender_status
        live = query_defender_status()
        return {
            **live,
            "engine_version": engine,
            "signature_version": signature,
            "platform_version": platform,
        }
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"available": False}


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def _defender_policy_active(winreg: Any) -> bool:
    checks = (
        (r"SOFTWARE\Microsoft\Windows Defender", "DisableAntiVirus"),
        (r"SOFTWARE\Microsoft\Windows Defender", "DisableAntiSpyware"),
        (r"SOFTWARE\Microsoft\Windows Defender", "PassiveMode"),
        (r"SOFTWARE\Microsoft\Windows Defender\Real-Time Protection", "DisableRealtimeMonitoring"),
        (r"SOFTWARE\Microsoft\Windows Defender\Real-Time Protection", "DisableOnAccessProtection"),
        (r"SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiVirus"),
        (r"SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiSpyware"),
        (r"SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection", "DisableRealtimeMonitoring"),
        (r"SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection", "DisableOnAccessProtection"),
        (r"SOFTWARE\Policies\Microsoft\Windows Advanced Threat Protection", "ForceDefenderPassiveMode"),
    )
    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    for key_path, value_name in checks:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
                value = int(winreg.QueryValueEx(key, value_name)[0])
        except FileNotFoundError:
            continue
        except (OSError, TypeError, ValueError):
            return False
        if value != 0:
            return False
    return True


def _defender_service_running() -> bool:
    """Query WinDefend with the Service Control Manager API (state 4 = running)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class ServiceStatusProcess(ctypes.Structure):
            _fields_ = [
                ("service_type", wintypes.DWORD), ("current_state", wintypes.DWORD),
                ("controls_accepted", wintypes.DWORD), ("win32_exit_code", wintypes.DWORD),
                ("service_specific_exit_code", wintypes.DWORD), ("check_point", wintypes.DWORD),
                ("wait_hint", wintypes.DWORD), ("process_id", wintypes.DWORD),
                ("service_flags", wintypes.DWORD),
            ]

        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.OpenSCManagerW.restype = wintypes.HANDLE
        api.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
        api.OpenServiceW.restype = wintypes.HANDLE
        api.QueryServiceStatusEx.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.QueryServiceStatusEx.restype = wintypes.BOOL
        api.CloseServiceHandle.argtypes = [wintypes.HANDLE]
        api.CloseServiceHandle.restype = wintypes.BOOL
        manager = api.OpenSCManagerW(None, None, 0x0001)
        if not manager:
            return False
        service = None
        try:
            service = api.OpenServiceW(manager, "WinDefend", 0x0004)
            if not service:
                return False
            status = ServiceStatusProcess()
            needed = wintypes.DWORD()
            ok = api.QueryServiceStatusEx(
                service, 0, ctypes.byref(status), ctypes.sizeof(status), ctypes.byref(needed)
            )
            return bool(ok and status.current_state == 4)
        finally:
            if service:
                api.CloseServiceHandle(service)
            api.CloseServiceHandle(manager)
    except (OSError, AttributeError):
        return False


def _status_complete(status: dict[str, Any]) -> bool:
    if status.get('source') == 'windows_security_center':
        active = status.get('product_state') == 'ON' and status.get('signature_status') == 'UP_TO_DATE'
    else:
        active = (status.get('active') and status.get('real_time_protection')
                  and str(status.get('running_mode') or '').casefold() == 'normal')
    return bool(
        status.get("available")
        and active
        and status.get("engine_version")
        and status.get("signature_version")
        and status.get("platform_version")
    )
