from __future__ import annotations

import socket
import subprocess
import sys
import json
import io
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import link_import
import link_download_worker


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://soundcloud.com/artist-name/track-name", "https://soundcloud.com/artist-name/track-name"),
    ],
)
def test_validate_url_canonicalizes_single_public_tracks(raw, canonical):
    assert link_import.validate_url(raw) == canonical


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "file:///etc/passwd",
        "https://user:password@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:444/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
        "https://soundcloud.com/artist/sets/album",
        "https://evil.example/watch?v=dQw4w9WgXcQ",
    ],
)
def test_validate_url_rejects_unsafe_or_non_track_urls(url):
    with pytest.raises(link_import.LinkImportError):
        link_import.validate_url(url)


def test_outbound_guard_rejects_private_and_mixed_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *, type):
        assert type == socket.SOCK_STREAM
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    monkeypatch.setattr(link_import.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(link_import.LinkImportError, match="public"):
        link_import._validate_outbound_url("https://rr1---sn.example.googlevideo.com/videoplayback")


def test_outbound_guard_re_resolves_every_request(monkeypatch):
    answers = iter(["142.250.72.14", "127.0.0.1"])

    def fake_getaddrinfo(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    monkeypatch.setattr(link_import.socket, "getaddrinfo", fake_getaddrinfo)
    link_import._validate_outbound_url("https://i.ytimg.com/vi/id/default.jpg")
    with pytest.raises(link_import.LinkImportError, match="public"):
        link_import._validate_outbound_url("https://i.ytimg.com/vi/id/default.jpg")


def test_https_transport_connects_to_the_validated_address(monkeypatch):
    seen = {}

    class FakeConnection:
        sock = None

        def __init__(self, host, port, address, timeout):
            seen.update(host=host, port=port, address=address)

        def request(self, method, target, body=None, headers=None):
            seen.update(method=method, target=target, host_header=headers["Host"])

        def getresponse(self):
            return SimpleNamespace(
                status=seen.get("response_status", 200),
                reason="Forbidden" if seen.get("response_status") == 403 else "OK",
                headers={"Content-Length": "4"},
                read=lambda size=-1: b"data",
                close=lambda: None,
            )

        def close(self):
            pass

    monkeypatch.setattr(link_import, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(
        link_import.socket,
        "getaddrinfo",
        lambda host, port, *, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", port))],
    )
    response = link_import._open_https(
        SimpleNamespace(url="https://i.ytimg.com/image", method="GET", headers={}),
        budget=link_import._TransportBudget(
            deadline=link_import.time.monotonic() + 5,
            max_media_bytes=16,
        ),
    )

    assert response.read() == b"data"
    from yt_dlp.networking import Response

    assert isinstance(response, Response)
    assert seen == {
        "host": "i.ytimg.com",
        "port": 443,
        "address": "142.250.72.14",
        "method": "GET",
        "target": "/image",
        "host_header": "i.ytimg.com",
    }

    seen["response_status"] = 403
    from yt_dlp.networking.exceptions import HTTPError

    with pytest.raises(HTTPError) as caught:
        link_import._open_https(
            SimpleNamespace(url="https://i.ytimg.com/denied", method="GET", headers={}),
            budget=link_import._TransportBudget(
                deadline=link_import.time.monotonic() + 5,
                max_media_bytes=16,
            ),
        )
    assert caught.value.status == 403


class _FakeResponse:
    headers = {"Content-Length": "4"}

    def read(self, size=-1):
        return b"data"


class _FakeYoutubeDL:
    last_options = None
    network_urls = []
    result = {
        "id": "abc", "title": "Safe title", "ext": "m4a",
        "webpage_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "artist": "Safe artist", "album": "Safe album", "genre": "Rock",
        "release_year": 2024, "track": "Safe track", "track_number": 7,
    }

    def __init__(self, options):
        _FakeYoutubeDL.last_options = options
        self.params = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def urlopen(self, request):
        type(self).network_urls.append(request.url)
        return _FakeResponse()

    def extract_info(self, url, download=True):
        self.urlopen(SimpleNamespace(url="https://r1---sn.test.googlevideo.com/videoplayback"))
        target = Path(self.params["outtmpl"] % {"id": "../../escape", "ext": "m4a"})
        target.write_bytes(b"data")
        for hook in self.params["progress_hooks"]:
            hook({"status": "finished", "filename": str(target), "downloaded_bytes": 4, "total_bytes": 4})
        return dict(type(self).result)

    def prepare_filename(self, info):
        return self.params["outtmpl"] % info


def _install_fake_yt_dlp(monkeypatch):
    _FakeYoutubeDL.network_urls = []
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_FakeYoutubeDL))
    monkeypatch.setitem(
        sys.modules,
        "yt_dlp.utils",
        SimpleNamespace(DownloadError=RuntimeError, MaxDownloadsReached=RuntimeError),
    )
    def fake_open(request, *, budget):
        _FakeYoutubeDL.network_urls.append(request.url)
        link_import._validate_outbound_url(request.url)
        return _FakeResponse()

    monkeypatch.setattr(link_import, "_open_https", fake_open)
    monkeypatch.setattr(link_import, "_fixed_deno_path", lambda: Path("C:/fixed-runtime/deno.exe"))
    monkeypatch.setattr(link_import, "_restricted_deno_environment", lambda: nullcontext())
    monkeypatch.setattr(
        link_import.socket,
        "getaddrinfo",
        lambda host, port, *, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", port))],
    )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="yt-dlp is given the Windows deno.exe path; pathlib renders that path differently off Windows",
)
def test_download_uses_locked_down_yt_dlp_and_returns_media(tmp_path, monkeypatch):
    _install_fake_yt_dlp(monkeypatch)
    cache = tmp_path / "managed" / "reservation"
    cache.mkdir(parents=True)
    updates = []

    media = link_import._download_link_inprocess(
        "https://youtu.be/dQw4w9WgXcQ", cache, max_bytes=16, progress=updates.append
    )

    assert media.path == cache / "audio.m4a"
    assert media.name == "Safe track.m4a"
    assert media.metadata["provider"] == "youtube"
    assert media.metadata["artist"] == "Safe artist"
    assert media.metadata["album"] == "Safe album"
    assert media.metadata["genre"] == "Rock"
    assert media.metadata["year"] == 2024
    assert media.metadata["track"] == 7
    assert media.metadata["title"] == "Safe track"
    assert media.metadata["uploader"] == ""
    assert media.path.read_bytes() == b"data"
    options = _FakeYoutubeDL.last_options
    assert options["noplaylist"] is True
    assert "max_downloads" not in options
    assert options["noprogress"] is True
    assert options["fixup"] == "never"
    assert options["allowed_extractors"] == ["youtube", "soundcloud"]
    assert options["enable_file_urls"] is False
    assert "extractor_args" not in options
    assert options["cookiefile"] is None
    assert options["usenetrc"] is False
    assert options["postprocessors"] == []
    assert options["remote_components"] == set()
    assert options["js_runtimes"] == {"deno": {"path": "C:\\fixed-runtime\\deno.exe"}}
    assert "webm" not in options["format"]
    assert updates
    assert _FakeYoutubeDL.network_urls == ["https://r1---sn.test.googlevideo.com/videoplayback"]


def test_secure_downloader_bypasses_and_rejects_postprocessors():
    budget = link_import._TransportBudget(link_import.time.monotonic() + 5, 16)
    secure = link_import._secure_ydl_class(_FakeYoutubeDL, budget, lambda request, *, budget: _FakeResponse())({})
    info = {"filepath": "audio.m4a"}
    assert secure.post_process("audio.m4a", info, {}) is info
    with pytest.raises(link_import.LinkImportError, match="post-processing"):
        secure.run_pp(object(), info)


def test_download_rejects_rebound_media_host_before_transport(tmp_path, monkeypatch):
    _install_fake_yt_dlp(monkeypatch)
    cache = tmp_path / "managed" / "reservation"
    cache.mkdir(parents=True)
    monkeypatch.setattr(
        link_import.socket,
        "getaddrinfo",
        lambda host, port, *, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
    )

    with pytest.raises(link_import.LinkImportError, match="public"):
        link_import._download_link_inprocess("https://youtu.be/dQw4w9WgXcQ", cache, max_bytes=16)
    assert not list(cache.iterdir())


def test_download_removes_oversized_output(tmp_path, monkeypatch):
    _install_fake_yt_dlp(monkeypatch)
    cache = tmp_path / "managed" / "reservation"
    cache.mkdir(parents=True)

    with pytest.raises(link_import.LinkImportError, match="size limit"):
        link_import._download_link_inprocess("https://youtu.be/dQw4w9WgXcQ", cache, max_bytes=3)
    assert not list(cache.iterdir())


def test_download_rejects_live_results_and_removes_output(tmp_path, monkeypatch):
    _install_fake_yt_dlp(monkeypatch)
    cache = tmp_path / "managed" / "reservation"
    cache.mkdir(parents=True)
    monkeypatch.setattr(_FakeYoutubeDL, "result", {**_FakeYoutubeDL.result, "is_live": True})

    with pytest.raises(link_import.LinkImportError, match="Live"):
        link_import._download_link_inprocess("https://youtu.be/dQw4w9WgXcQ", cache, max_bytes=16)
    assert not list(cache.iterdir())


def test_download_does_not_expose_provider_error_details(tmp_path, monkeypatch):
    _install_fake_yt_dlp(monkeypatch)
    cache = tmp_path / "managed" / "reservation"
    cache.mkdir(parents=True)
    secret = "https://googlevideo.example/file?signature=TOP_SECRET"
    monkeypatch.setattr(_FakeYoutubeDL, "extract_info", lambda self, url, download=True: (_ for _ in ()).throw(RuntimeError(secret)))

    with pytest.raises(link_import.LinkImportError) as caught:
        link_import._download_link_inprocess("https://youtu.be/dQw4w9WgXcQ", cache, max_bytes=16)
    assert "TOP_SECRET" not in str(caught.value)
    assert "public" in str(caught.value)


def test_download_requires_existing_real_directory(tmp_path):
    with pytest.raises(link_import.LinkImportError, match="directory"):
        link_import._download_link_inprocess("https://youtu.be/dQw4w9WgXcQ", tmp_path / "missing", max_bytes=16)


@pytest.mark.parametrize("url", ["https://[::1", "https://[not-ip]/watch?v=dQw4w9WgXcQ"])
def test_validate_url_turns_malformed_ipv6_into_link_error(url):
    with pytest.raises(link_import.LinkImportError):
        link_import.validate_url(url)


def test_youtube_download_requires_the_fixed_packaged_deno(tmp_path, monkeypatch):
    _install_fake_yt_dlp(monkeypatch)
    cache = tmp_path / "managed" / "reservation"
    cache.mkdir(parents=True)
    monkeypatch.setattr(
        link_import,
        "_fixed_deno_path",
        lambda: (_ for _ in ()).throw(link_import.LinkImportError("packaged Deno runtime is missing")),
    )

    with pytest.raises(link_import.LinkImportError, match="packaged Deno"):
        link_import._download_link_inprocess("https://youtu.be/dQw4w9WgXcQ", cache, max_bytes=16)


def test_deno_environment_drops_user_runtime_and_proxy_overrides(monkeypatch):
    monkeypatch.setattr(
        link_import.os,
        "environ",
        {
            "SYSTEMROOT": "C:\\Windows",
            "TEMP": "C:\\safe-temp",
            "PATH": "C:\\attacker-bin",
            "DENO_DIR": "C:\\attacker-cache",
            "DENO_AUTH_TOKENS": "secret",
            "NODE_OPTIONS": "--require=evil.js",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
        },
    )

    assert link_import._sanitized_runtime_environment() == {
        "SYSTEMROOT": "C:\\Windows",
        "TEMP": "C:\\safe-temp",
        "DENO_NO_UPDATE_CHECK": "1",
        "NO_COLOR": "1",
    }


def test_packaged_deno_denies_file_network_and_process_permissions():
    try:
        deno = link_import._fixed_deno_path()
    except link_import.LinkImportError:
        pytest.skip("packaged Deno has not been staged yet")
    script = """
const checks = [];
try { await Deno.readTextFile('C:/Windows/win.ini'); checks.push(false); } catch { checks.push(true); }
try { await fetch('https://example.com/'); checks.push(false); } catch { checks.push(true); }
try { new Deno.Command('cmd.exe').outputSync(); checks.push(false); } catch { checks.push(true); }
console.log(JSON.stringify(checks));
"""
    result = subprocess.run(
        [
            str(deno), "run", "--ext=js", "--no-code-cache", "--no-prompt", "--no-remote",
            "--no-lock", "--node-modules-dir=none", "--no-config", "--no-npm", "--cached-only", "-",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        env=link_import._sanitized_runtime_environment(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[true,true,true]"


def test_worker_progress_is_bounded_compatible_and_coalesced(monkeypatch):
    emitted = []
    monkeypatch.setattr(link_download_worker, "emit", emitted.append)
    progress = link_download_worker.ProgressEmitter()
    progress({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
    progress({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
    progress({"status": "downloading", "downloaded_bytes": 2 << 40, "total_bytes": 2 << 40})
    progress({"status": "downloading", "downloaded_bytes": 512, "total_bytes": None})
    progress({"status": "downloading", "downloaded_bytes": 1024, "total_bytes": None})

    assert len(emitted) == 3
    assert emitted[0]["value"] == {
        "status": "downloading", "percent": 1, "downloaded_bytes": 1, "total_bytes": 100,
    }
    assert emitted[1]["value"]["downloaded_bytes"] == 1 << 40
    assert emitted[1]["value"]["total_bytes"] == 1 << 40
    assert emitted[2]["value"]["downloaded_bytes"] == 512
    assert emitted[2]["value"]["total_bytes"] == 0


def test_unknown_size_progress_never_exceeds_protocol_event_budget(monkeypatch):
    emitted = []
    monkeypatch.setattr(link_download_worker, "emit", emitted.append)
    progress = link_download_worker.ProgressEmitter()
    for downloaded in range(0, 501 * 1024 * 1024, 1024 * 1024):
        progress({"status": "downloading", "downloaded_bytes": downloaded})
    progress({"status": "finished", "downloaded_bytes": 500 * 1024 * 1024})

    assert len(emitted) <= 101
    assert emitted[-1]["value"]["status"] == "finished"
    encoded = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in emitted)
    assert len(encoded) < 32768


def test_failure_cleanup_kills_waits_and_closes_pipes():
    class Stream:
        closed = False

        def close(self):
            self.closed = True

    class Process:
        stdin = Stream()
        stdout = Stream()
        waits = 0

        def wait(self, timeout):
            self.waits += 1
            return 1

        def kill(self):
            raise AssertionError("containment kill should be sufficient")

    class Containment:
        killed = False
        closed = False

        def kill(self):
            self.killed = True

        def close(self):
            self.closed = True

    process = Process()
    containment = Containment()
    link_import._kill_reap_and_close(process, containment)

    assert containment.killed and containment.closed
    assert process.waits == 1
    assert process.stdin.closed and process.stdout.closed


def test_progress_callback_exception_reaps_owned_worker(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(
                b'{"type":"progress","value":{"status":"downloading","downloaded_bytes":1,"total_bytes":2}}\n'
            )
            self.returncode = None
            self.waited = False

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.waited = True
            self.returncode = 1
            return 1

        def kill(self):
            self.returncode = 1

    class FakeContainment:
        instance = None

        def __init__(self, process):
            type(self).instance = self
            self.killed = False
            self.closed = False

        def kill(self):
            self.killed = True

        def close(self):
            self.closed = True

    process = FakeProcess()
    monkeypatch.setattr(link_import.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(link_import, "_ProcessContainment", FakeContainment)
    cache = tmp_path / "reservation"
    cache.mkdir()

    with pytest.raises(RuntimeError, match="database closed"):
        link_import.download_link(
            "https://youtu.be/dQw4w9WgXcQ",
            cache,
            16,
            progress=lambda value: (_ for _ in ()).throw(RuntimeError("database closed")),
        )

    assert FakeContainment.instance.killed and FakeContainment.instance.closed
    assert process.waited
    assert process.stdin.closed and process.stdout.closed
