"""Constrained YouTube/SoundCloud downloader for the managed import cache.

This stage only retrieves opaque bytes.  Decoding and conversion belong after the
caller's Defender scan gate.
"""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit


MAX_RESPONSE_BYTES = 500 * 1024 * 1024
MAX_SECONDS = 10 * 60
MAX_REDIRECTS = 5
MAX_REQUESTS = 128
MAX_METADATA_BYTES = 16 * 1024 * 1024
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_EXTENSIONS = {"m4a", "mp3", "ogg"}
_PROVIDER_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "music.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "soundcloud.com",
    "www.soundcloud.com",
    "m.soundcloud.com",
}
_OUTBOUND_SUFFIXES = (
    ".youtube.com",
    ".youtube-nocookie.com",
    ".googlevideo.com",
    ".ytimg.com",
    ".youtubei.googleapis.com",
    ".soundcloud.com",
    ".sndcdn.com",
)
_OUTBOUND_EXACT = _PROVIDER_HOSTS | {
    "youtubei.googleapis.com",
    "i.ytimg.com",
    "yt3.ggpht.com",
    "api-v2.soundcloud.com",
    "cf-media.sndcdn.com",
    "cf-hls-media.sndcdn.com",
}
_DENO_ENVIRONMENT_LOCK = threading.RLock()
_RUNTIME_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")


class LinkImportError(RuntimeError):
    """A safe, user-actionable link import failure."""


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    name: str
    metadata: dict[str, Any]


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise LinkImportError("Enter a valid YouTube or SoundCloud track URL.")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise LinkImportError("Enter a valid YouTube or SoundCloud track URL.") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        raise LinkImportError("Only credential-free HTTPS links are allowed.")
    try:
        port = parts.port
    except ValueError as exc:
        raise LinkImportError("The link contains an invalid port.") from exc
    if port not in (None, 443) or host not in _PROVIDER_HOSTS:
        raise LinkImportError("Only standard YouTube and SoundCloud HTTPS links are allowed.")
    if parts.fragment:
        raise LinkImportError("URL fragments are not allowed.")

    query = parse_qs(parts.query, keep_blank_values=True)
    if host == "youtu.be":
        video_id = parts.path.strip("/")
    elif host.endswith("youtube.com"):
        if parts.path != "/watch":
            raise LinkImportError("Use a single YouTube video link, not a channel or playlist.")
        values = query.get("v", [])
        video_id = values[0] if len(values) == 1 else ""
    else:
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) != 2 or not all(_SLUG.fullmatch(segment) for segment in segments):
            raise LinkImportError("Use a single public SoundCloud track link, not a set or profile.")
        if query:
            raise LinkImportError("SoundCloud track links may not contain query options.")
        return urlunsplit(("https", "soundcloud.com", f"/{segments[0]}/{segments[1]}", "", ""))

    if not _VIDEO_ID.fullmatch(video_id) or "list" in query:
        raise LinkImportError("Use a single YouTube video link, not a playlist.")
    return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"


def _host_allowed(host: str) -> bool:
    return host in _OUTBOUND_EXACT or any(host.endswith(suffix) for suffix in _OUTBOUND_SUFFIXES)


def _resolve_public(host: str, port: int = 443) -> list[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LinkImportError(f"Could not safely resolve {host}.") from exc
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise LinkImportError(f"Could not safely resolve {host}.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise LinkImportError("The provider returned an invalid network address.") from exc
        if not ip.is_global:
            raise LinkImportError("The provider resolved to a non-public network address.")
    return addresses


def _validate_outbound_url(url: str) -> tuple[str, int, list[str]]:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise LinkImportError("A provider request attempted an invalid URL.") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        raise LinkImportError("A provider request attempted an unsafe URL.")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise LinkImportError("A provider request attempted an invalid port.") from exc
    if port != 443 or not _host_allowed(host):
        raise LinkImportError(f"The provider attempted a request to an unapproved host: {host}.")
    return host, port, _resolve_public(host, port)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass
class _TransportBudget:
    deadline: float
    max_media_bytes: int
    media_bytes: int = 0
    metadata_bytes: int = 0
    requests: int = 0

    def start_request(self) -> None:
        if time.monotonic() >= self.deadline:
            raise LinkImportError("The link download timed out after 10 minutes.")
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise LinkImportError("The provider made too many network requests.")

    def consume(self, amount: int, *, media: bool) -> None:
        if time.monotonic() >= self.deadline:
            raise LinkImportError("The link download timed out after 10 minutes.")
        if media:
            self.media_bytes += amount
            if self.media_bytes > self.max_media_bytes:
                raise LinkImportError("The link download exceeded the configured size limit.")
        else:
            self.metadata_bytes += amount
            if self.metadata_bytes > MAX_METADATA_BYTES:
                raise LinkImportError("Provider metadata exceeded the safe response limit.")


def _is_media_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host.endswith(".googlevideo.com") or host.endswith(".sndcdn.com")


class _BoundedReader(io.RawIOBase):
    def __init__(self, response: http.client.HTTPResponse, connection: http.client.HTTPSConnection, budget: _TransportBudget, media: bool):
        self._response = response
        self._connection = connection
        self._budget = budget
        self._media = media

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        remaining_seconds = self._budget.deadline - time.monotonic()
        if remaining_seconds <= 0:
            self.close()
            raise LinkImportError("The link download timed out after 10 minutes.")
        if self._connection.sock is not None:
            self._connection.sock.settimeout(max(0.1, min(30.0, remaining_seconds)))
        remaining = (
            self._budget.max_media_bytes - self._budget.media_bytes
            if self._media
            else MAX_METADATA_BYTES - self._budget.metadata_bytes
        )
        requested = remaining + 1 if size is None or size < 0 else min(size, remaining + 1)
        data = self._response.read(requested)
        try:
            self._budget.consume(len(data), media=self._media)
        except LinkImportError:
            self.close()
            raise
        if not data:
            self.close()
        return data

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._response.close()
        finally:
            self._connection.close()
            super().close()


def _request_value(request: Any, name: str, default: Any = None) -> Any:
    value = getattr(request, name, default)
    return value() if callable(value) else value


def _open_https(request: Any, *, budget: _TransportBudget):
    from yt_dlp.networking import Response
    from yt_dlp.networking.exceptions import HTTPError

    url = _request_value(request, "url") or _request_value(request, "full_url")
    method = (_request_value(request, "method") or _request_value(request, "get_method", "GET")).upper()
    body = _request_value(request, "data")
    headers = dict(_request_value(request, "headers", {}) or {})
    headers["Accept-Encoding"] = "identity"
    for _ in range(MAX_REDIRECTS + 1):
        budget.start_request()
        host, port, addresses = _validate_outbound_url(url)
        timeout = max(0.1, min(30.0, budget.deadline - time.monotonic()))
        connection = _PinnedHTTPSConnection(host, port, addresses[0], timeout)
        parts = urlsplit(url)
        target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            connection.request(method, target, body=body, headers={**headers, "Host": host})
            response = connection.getresponse()
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            connection.close()
            raise LinkImportError(f"Secure connection to {host} failed.") from exc
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            connection.close()
            if not location:
                raise LinkImportError("The provider returned an invalid redirect.")
            url = urljoin(url, location)
            if response.status == 303:
                method, body = "GET", None
            continue
        length = response.headers.get("Content-Length")
        media = _is_media_url(url)
        response_limit = budget.max_media_bytes - budget.media_bytes if media else MAX_METADATA_BYTES - budget.metadata_bytes
        if length:
            try:
                parsed_length = int(length)
                if parsed_length < 0 or parsed_length > response_limit:
                    response.close()
                    connection.close()
                    raise LinkImportError("A provider response exceeded the configured size limit.")
            except ValueError as exc:
                response.close()
                connection.close()
                raise LinkImportError("The provider returned an invalid response size.") from exc
        wrapped = Response(
            _BoundedReader(response, connection, budget, media),
            url=url,
            headers=dict(response.headers.items()),
            status=response.status,
            reason=response.reason,
        )
        if response.status >= 400:
            raise HTTPError(wrapped)
        return wrapped
    raise LinkImportError("The provider returned too many redirects.")


def _safe_name(title: str, extension: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:180] or "Imported track"
    return f"{cleaned}.{extension}"


def _cleanup(directory: Path, before: set[Path]) -> None:
    for candidate in directory.iterdir():
        if candidate not in before and candidate.is_file():
            try:
                candidate.unlink()
            except OSError:
                pass


def _fixed_deno_path() -> Path:
    repository = Path(__file__).resolve().parent.parent
    candidates = (repository / "deno" / "deno.exe", repository.parent / "tools" / "deno" / "deno.exe")
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    raise LinkImportError(
        "The packaged Deno runtime is missing. Repair Night Ops before importing YouTube links."
    )


def _sanitized_runtime_environment() -> dict[str, str]:
    clean = {key: os.environ[key] for key in _RUNTIME_ENV_KEYS if os.environ.get(key)}
    clean.update({"DENO_NO_UPDATE_CHECK": "1", "NO_COLOR": "1"})
    return clean


@contextmanager
def _restricted_deno_environment():
    """Temporarily constrain yt-dlp's Deno provider, which otherwise copies os.environ."""
    try:
        from yt_dlp.extractor.youtube.jsc._builtin.deno import DenoJCP
    except ImportError as exc:
        raise LinkImportError("The installed yt-dlp package lacks the required Deno provider.") from exc

    with _DENO_ENVIRONMENT_LOCK:
        original = DenoJCP._get_env_options
        DenoJCP._get_env_options = lambda self: _sanitized_runtime_environment()
        try:
            yield
        finally:
            DenoJCP._get_env_options = original


def _secure_ydl_class(base, budget: _TransportBudget, module_open):
    class SecureYoutubeDL(base):
        def urlopen(self, request):
            return module_open(request, budget=budget)

        def run_pp(self, *args, **kwargs):
            raise LinkImportError("Media post-processing is blocked until after the Defender scan.")

        def post_process(self, filename, info, files_to_move=None):
            return info

    return SecureYoutubeDL


def _download_link_inprocess(
    url: str,
    directory: Path,
    max_bytes: int,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> DownloadedMedia:
    canonical = validate_url(url)
    is_youtube = "youtube.com" in canonical
    directory = Path(directory)
    if max_bytes <= 0 or max_bytes > MAX_RESPONSE_BYTES:
        raise LinkImportError("The requested download size limit is invalid.")
    if not directory.exists() or not directory.is_dir() or directory.is_symlink():
        raise LinkImportError("The managed download directory must already exist and may not be a link.")
    resolved_directory = directory.resolve(strict=True)
    before = set(directory.iterdir())
    deadline = time.monotonic() + MAX_SECONDS
    budget = _TransportBudget(deadline=deadline, max_media_bytes=max_bytes)

    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError, MaxDownloadsReached
    except ImportError as exc:
        raise LinkImportError("Link importing is unavailable because yt-dlp is not installed.") from exc

    deno_path = _fixed_deno_path() if is_youtube else None

    module_open = _open_https

    SecureYoutubeDL = _secure_ydl_class(yt_dlp.YoutubeDL, budget, module_open)

    def hook(status: dict[str, Any]) -> None:
        if time.monotonic() > deadline:
            raise LinkImportError("The link download timed out after 10 minutes.")
        downloaded = status.get("downloaded_bytes") or status.get("total_bytes") or 0
        if downloaded > max_bytes:
            raise LinkImportError("The link download exceeded the configured size limit.")
        if progress:
            progress(dict(status))

    options = {
        "outtmpl": str(resolved_directory / "audio.%(ext)s"),
        "format": "bestaudio[protocol^=http][ext=m4a]/bestaudio[protocol^=http][ext=mp3]/bestaudio[protocol^=http][ext=ogg]",
        "noplaylist": True,
        "allowed_extractors": ["youtube", "soundcloud"],
        "default_search": "error",
        "match_filter": lambda info, *, incomplete=False: "Live streams are not supported" if info.get("is_live") else None,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 0,
        "file_access_retries": 0,
        "continuedl": False,
        "overwrites": False,
        "nopart": True,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "netrc_location": None,
        "proxy": "",
        "enable_file_urls": False,
        "geo_bypass": False,
        "cachedir": False,
        "postprocessors": [],
        "fixup": "never",
        "external_downloader": None,
        "allow_unplayable_formats": False,
        "remote_components": set(),
        "js_runtimes": {"deno": {"path": str(deno_path)}} if deno_path else {},
        "plugin_dirs": [],
        "ignoreconfig": True,
        "progress_hooks": [hook],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
    }
    try:
        runtime_guard = _restricted_deno_environment() if is_youtube else nullcontext()
        with runtime_guard:
            with SecureYoutubeDL(options) as ydl:
                info = ydl.extract_info(canonical, download=True)
                if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                    raise LinkImportError("The link did not resolve to one track.")
                if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
                    raise LinkImportError("Live or scheduled media cannot be imported.")
                path = Path(ydl.prepare_filename(info)).resolve(strict=True)
    except LinkImportError:
        _cleanup(directory, before)
        raise
    except (DownloadError, MaxDownloadsReached, OSError, ValueError):
        _cleanup(directory, before)
        raise LinkImportError(
            "The provider could not download this public track. Check that it is public, playable, and not restricted."
        ) from None

    if path.parent != resolved_directory or path.suffix.lower().lstrip(".") not in _SAFE_EXTENSIONS:
        _cleanup(directory, before)
        raise LinkImportError("The provider returned an unsupported media file.")
    if path.stat().st_size > max_bytes:
        _cleanup(directory, before)
        raise LinkImportError("The link download exceeded the configured size limit.")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    extension = path.suffix.lower().lstrip(".")
    provider = "youtube" if is_youtube else "soundcloud"
    def bounded(value: Any, limit: int = 512) -> str:
        return str(value or "")[:limit]

    release_year_raw = info.get("release_year") or bounded(info.get("release_date"), 8)[:4]
    try:
        release_year = int(release_year_raw) if release_year_raw else None
    except (TypeError, ValueError):
        release_year = None
    metadata = {
        "provider": provider,
        "source_url": canonical,
        "id": bounded(info.get("id"), 128),
        "title": bounded(info.get("track") or info.get("title") or "Imported track"),
        "artist": bounded(info.get("artist") or info.get("creator")),
        "uploader": bounded(info.get("uploader")),
        "album": bounded(info.get("album")),
        "genre": bounded(info.get("genre"), 128),
        "year": release_year,
        "track": info.get("track_number"),
        "duration": info.get("duration"),
        "extension": extension,
    }
    return DownloadedMedia(path=path, name=_safe_name(metadata["title"], extension), metadata=metadata)


class _ProcessContainment:
    def __init__(self, process: subprocess.Popen[bytes]):
        self._process = process
        self._handle = None
        if os.name == "nt":
            self._assign_windows_job()

    def _assign_windows_job(self) -> None:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(self._process._handle)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        self._handle = job
        self._close_handle = kernel32.CloseHandle

    def kill(self) -> None:
        if self._handle:
            self._close_handle(self._handle)
            self._handle = None
        elif os.name != "nt":
            try:
                os.killpg(self._process.pid, 9)
            except OSError:
                pass
        if self._process.poll() is None:
            self._process.kill()

    def close(self) -> None:
        if self._handle:
            self._close_handle(self._handle)
            self._handle = None


def _worker_environment() -> dict[str, str]:
    clean = _sanitized_runtime_environment()
    clean.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return clean


def _kill_reap_and_close(process: subprocess.Popen[bytes], containment: _ProcessContainment) -> None:
    containment.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed:
                stream.close()
        containment.close()


def download_link(
    url: str,
    directory: Path,
    max_bytes: int,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> DownloadedMedia:
    canonical = validate_url(url)
    directory = Path(directory)
    if not directory.exists() or not directory.is_dir() or directory.is_symlink():
        raise LinkImportError("The managed download directory must already exist and may not be a link.")
    directory = directory.resolve(strict=True)
    worker = Path(__file__).resolve().with_name("link_download_worker.py")
    if not worker.is_file():
        raise LinkImportError("The isolated link download worker is missing. Repair Night Ops and retry.")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_worker_environment(),
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        containment = _ProcessContainment(process)
    except (OSError, subprocess.SubprocessError) as exc:
        if "process" in locals():
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()
        raise LinkImportError("Night Ops could not securely isolate the link download worker.") from None

    messages: queue.Queue[bytes | None] = queue.Queue()

    def read_output() -> None:
        try:
            assert process.stdout is not None
            total = 0
            while True:
                line = process.stdout.readline(32769)
                if not line:
                    break
                total += len(line)
                if total > 32768 or len(line) > 32768:
                    messages.put(b'{"type":"protocol_error"}')
                    break
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    deadline = time.monotonic() + MAX_SECONDS
    result: dict[str, Any] | None = None
    try:
        reader.start()
        assert process.stdin is not None
        request = json.dumps({"url": canonical, "directory": str(directory), "max_bytes": max_bytes}) + "\n"
        process.stdin.write(request.encode("utf-8"))
        process.stdin.close()
        output_done = False
        while not output_done or process.poll() is None:
            if time.monotonic() >= deadline:
                raise LinkImportError("The link download timed out after 10 minutes.")
            try:
                line = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                output_done = True
                continue
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise LinkImportError("The isolated download worker returned an invalid response.") from None
            if message.get("type") == "progress" and progress:
                progress(message.get("value") or {})
            elif message.get("type") == "result":
                result = message
            elif message.get("type") in {"error", "protocol_error"}:
                raise LinkImportError(message.get("message") or "The isolated download worker failed safely.")
        if process.returncode != 0 or result is None:
            raise LinkImportError("The isolated download worker could not import this public track.")
    except BaseException:
        _kill_reap_and_close(process, containment)
        raise
    for stream in (process.stdin, process.stdout):
        if stream is not None and not stream.closed:
            stream.close()
    containment.close()
    path = Path(result["path"]).resolve(strict=True)
    if path.parent != directory:
        raise LinkImportError("The isolated worker returned a file outside the managed cache.")
    return DownloadedMedia(path=path, name=str(result["name"])[:256], metadata=dict(result["metadata"]))
