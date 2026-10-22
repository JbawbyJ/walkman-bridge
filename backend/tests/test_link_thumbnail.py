"""Provider fixtures exercise the real pinned transport; no network or decoder."""
from __future__ import annotations

import io
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp

import link_import
import link_download_worker


PNG = b"\x89PNG\r\n\x1a\n" + b"opaque, unscanned fixture"
IMAGE_URL = "https://i1.sndcdn.com/track?secret=not-for-metadata"


@pytest.fixture
def provider(tmp_path, monkeypatch):
    info = {"id": "42", "title": "動画 title", "track": "曲名", "artist": "演奏者",
            "album": "アルバム", "genre": "Jazz", "ext": "m4a", "thumbnail": IMAGE_URL,
            "_nightops_track_artwork": True}
    responses = {"/audio": (200, {}, b"audio"), "/track?secret=not-for-metadata": (200, {}, PNG)}
    calls = []

    class Connection:
        sock = None

        def __init__(self, host, port, address, timeout):
            self.host = host

        def request(self, method, target, body=None, headers=None):
            calls.append((self.host, target))
            self.target = target

        def getresponse(self):
            status, headers, content = responses[self.target]
            stream = io.BytesIO(content)
            return SimpleNamespace(status=status, reason="fixture", headers=headers,
                                   read=stream.read, close=stream.close)

        def close(self):
            pass

    class Provider:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add_info_extractor(self, extractor):
            self.extractor = extractor

        def extract_info(self, url, download=True):
            with self.urlopen(SimpleNamespace(url="https://cf-media.sndcdn.com/audio")) as response:
                (tmp_path / "audio.m4a").write_bytes(response.read())
            return dict(info)

        def prepare_filename(self, result):
            return str(tmp_path / "audio.m4a")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Provider)
    monkeypatch.setattr(link_import, "_PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(link_import.socket, "getaddrinfo", lambda host, port, **kw:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", port))])

    def run(limit=8 * 1024 * 1024):
        return link_import._download_link_inprocess("https://soundcloud.com/artist/track", tmp_path, limit)

    return SimpleNamespace(info=info, responses=responses, calls=calls, run=run, directory=tmp_path)


def test_selected_thumbnail_is_opaque_and_provenance_is_explicit(provider):
    result = provider.run()
    assert result.artwork_path == provider.directory / "thumbnail.png"
    assert result.artwork_path.read_bytes() == PNG
    assert result.metadata["title"] == "曲名"
    assert result.metadata["metadata_provenance"] == {
        "title": "provider.soundcloud.track", "artist": "provider.soundcloud.artist",
        "album": "provider.soundcloud.album", "genre": "provider.soundcloud.genre",
    }
    assert result.metadata["artwork_provenance"] == "provider.soundcloud.artwork_url"
    assert result.metadata["artwork_status"] == "downloaded"
    assert "not-for-metadata" not in json.dumps(result.metadata)


def test_uploader_creator_are_not_recording_artist(provider):
    provider.info.update(artist=None, creator="Video creator", uploader="Channel", album=None, genre=None)
    provider.info.pop("thumbnail")
    result = provider.run()
    assert result.metadata["artist"] == ""
    assert result.metadata["album"] == ""
    assert result.metadata["genre"] == ""
    assert result.metadata["metadata_provenance"] == {"title": "provider.soundcloud.track"}
    assert result.artwork_path is None
    assert result.metadata["artwork_status"] == "missing"


def test_thumbnail_list_fallback_records_actual_source(provider):
    provider.info.pop("thumbnail")
    provider.info["thumbnails"] = [{"url": IMAGE_URL, "width": 500, "height": 500}]
    result = provider.run()
    assert result.artwork_path.read_bytes() == PNG
    assert result.metadata["artwork_provenance"] == "provider.soundcloud.artwork_url"


def test_normalized_avatar_without_raw_track_artwork_is_excluded(provider):
    provider.info["_nightops_track_artwork"] = False
    result = provider.run()
    assert result.artwork_path is None
    assert result.metadata["artwork_reason"] == "no_confirmed_track_artwork"
    assert provider.calls == [("cf-media.sndcdn.com", "/audio")]


def test_explicit_artists_list_and_missing_title_are_not_fabricated(provider):
    provider.info.update(artist=None, artists=["歌手", "Band"], track=None, title=None)
    result = provider.run()
    assert result.metadata["artist"] == "歌手; Band"
    assert result.metadata["metadata_provenance"]["artist"] == "provider.soundcloud.artists"
    assert result.metadata["title"] == ""
    assert "title" not in result.metadata["metadata_provenance"]
    assert result.name == "Imported track.m4a"


def test_unbounded_or_fractional_provider_numbers_are_not_metadata(provider):
    provider.info.update(duration=10 ** 1000, track_number=1.5, disc_number=True, release_year="invalid")
    result = provider.run()
    assert result.metadata["duration"] is None
    assert result.metadata["track"] is None
    assert result.metadata["disc"] is None
    assert result.metadata["year"] is None


@pytest.mark.parametrize("artwork", [None, "not-a-url", "   ", "https://i1.sndcdn.com/artworks-fixture-large.jpg"])
def test_soundcloud_guard_preserves_only_original_track_artwork(artwork, monkeypatch):
    from yt_dlp.extractor.soundcloud import SoundcloudIE

    def original_extract(self, url):
        images = self._extract_thumbnails({"id": 42, "artwork_url": artwork,
                                          "user": {"avatar_url": "https://i1.sndcdn.com/avatars-fixture-large.jpg"}})
        return {"id": "42", "thumbnails": images}

    monkeypatch.setattr(SoundcloudIE, "_real_extract", original_extract)
    monkeypatch.setattr(SoundcloudIE, "_request_webpage", lambda *a, **kw: SimpleNamespace())
    extractor = link_import._track_artwork_soundcloud_class(SoundcloudIE)()
    result = extractor._real_extract("https://soundcloud.com/artist/track")
    confirmed_artwork = artwork == "https://i1.sndcdn.com/artworks-fixture-large.jpg"
    assert result["_nightops_track_artwork"] is confirmed_artwork
    if confirmed_artwork:
        assert result["thumbnails"]
        assert all("artworks-fixture" in image["url"] for image in result["thumbnails"])
    else:
        assert result["thumbnails"] == []


def test_soundcloud_changed_thumbnail_api_fails_artwork_closed():
    class ChangedExtractor:
        def _real_extract(self, url):
            return {"id": "42", "thumbnail": "https://i1.sndcdn.com/avatar.jpg"}

    extractor = link_import._track_artwork_soundcloud_class(ChangedExtractor)()
    result = extractor._real_extract("https://soundcloud.com/artist/track")
    assert result["_nightops_track_artwork"] is False


@pytest.mark.parametrize(("body", "filename"), [(b"\xff\xd8\xffopaque", "thumbnail.jpg"),
    (b"RIFF\x08\x00\x00\x00WEBPopaque", "thumbnail.webp")])
def test_image_extension_comes_from_signature(provider, body, filename):
    provider.responses["/track?secret=not-for-metadata"] = (200, {}, body)
    result = provider.run()
    assert result.artwork_path.name == filename
    assert result.artwork_path.read_bytes() == body


@pytest.mark.parametrize("case", ["html", "oversize", "budget", "http_error", "private_redirect", "unsafe_url"])
def test_optional_thumbnail_failure_preserves_audio_without_leaking_url(provider, case, monkeypatch):
    limit = 8 * 1024 * 1024
    if case == "html":
        provider.responses["/track?secret=not-for-metadata"] = (200, {}, b"<svg>not an image</svg>")
    elif case == "oversize":
        provider.responses["/track?secret=not-for-metadata"] = (200, {}, PNG + b"x" * (4 * 1024 * 1024))
    elif case == "budget":
        limit = len(PNG) + 4  # Audio + image exceeds shared budget by one byte.
    elif case == "http_error":
        provider.responses["/track?secret=not-for-metadata"] = (403, {}, b"denied")
    elif case == "private_redirect":
        provider.responses["/track?secret=not-for-metadata"] = (302, {"Location": "https://i.ytimg.com/private"}, b"")
        monkeypatch.setattr(link_import.socket, "getaddrinfo", lambda host, port, **kw:
                            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "i.ytimg.com" else "142.250.72.14", port))])
    else:
        provider.info["thumbnail"] = "http://evil.test/not-for-metadata"
    result = provider.run(limit)
    assert result.path.read_bytes() == b"audio"
    assert result.artwork_path is None
    assert result.metadata["artwork_status"] == "unavailable"
    assert len(result.metadata["artwork_reason"]) <= 64
    assert "not-for-metadata" not in json.dumps(result.metadata)
    assert list(provider.directory.iterdir()) == [result.path]


def test_thumbnail_never_overwrites_existing_file(provider):
    existing = provider.directory / "thumbnail.png"
    existing.write_bytes(b"existing unrelated bytes")
    result = provider.run()
    assert result.artwork_path is None
    assert existing.read_bytes() == b"existing unrelated bytes"


def test_worker_result_includes_raw_artwork_path(tmp_path, monkeypatch):
    media = SimpleNamespace(path=tmp_path / "audio.m4a", name="曲.m4a", metadata={}, artwork_path=tmp_path / "thumbnail.png")
    monkeypatch.setattr(link_download_worker, "_download_link_inprocess", lambda *a, **kw: media)
    monkeypatch.setattr(link_download_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps({
        "url": "https://soundcloud.com/artist/track", "directory": str(tmp_path), "max_bytes": 100}).encode())))
    messages = []
    monkeypatch.setattr(link_download_worker, "emit", messages.append)
    assert link_download_worker.main() == 0
    assert messages[0]["artwork_path"] == str(media.artwork_path)


def test_actual_isolated_worker_preserves_japanese_metadata_and_cache_path(tmp_path, monkeypatch):
    # Run the production spawn/containment/JSONL reader and real worker emitter.
    # Only replace the external provider request with deterministic opaque bytes.
    backend = str(Path(link_download_worker.__file__).resolve().parent)
    stub = tmp_path / "link_download_worker.py"
    stub.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {json.dumps(backend)})\n"
        "from link_download_worker import emit\n"
        "request = json.loads(sys.stdin.buffer.readline())\n"
        "path = Path(request['directory']) / 'audio.m4a'\n"
        "path.write_bytes(b'audio')\n"
        "emit({'type': 'result', 'path': str(path), 'name': '\\u66f2\\u540d.m4a', "
        "'metadata': {'title': '\\u66f2\\u540d', 'artist': '\\u6f14\\u594f\\u8005', "
        "'stdout_encoding': sys.stdout.encoding}, 'artwork_path': None})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(link_import, "__file__", str(tmp_path / "link_import.py"))
    cache = tmp_path / "\u65e5\u672c\u8a9e"
    cache.mkdir()
    result = link_import.download_link("https://soundcloud.com/artist/track", cache, 1024)
    assert result.path == cache / "audio.m4a"
    assert result.name == "\u66f2\u540d.m4a"
    assert result.metadata["title"] == "\u66f2\u540d"
    assert result.metadata["artist"] == "\u6f14\u594f\u8005"
    assert result.metadata["stdout_encoding"].lower().replace("-", "") == "utf8"


def test_maximum_unicode_metadata_and_progress_fit_worker_protocol(provider, monkeypatch):
    provider.info.update({key: "🎵" * 600 for key in
                          ("title", "track", "artist", "album", "album_artist", "genre", "uploader", "id")})
    result = provider.run()
    output = io.StringIO()
    monkeypatch.setattr(link_download_worker.sys, "stdout", output)
    progress = link_download_worker.ProgressEmitter()
    for index in range(101):
        progress({"status": "finished" if index == 100 else "downloading",
                  "downloaded_bytes": index * 1024 * 1024, "total_bytes": 100 * 1024 * 1024})
    link_download_worker.emit({"type": "result", "path": str(result.path), "name": result.name,
                               "metadata": result.metadata, "artwork_path": str(result.artwork_path)})
    assert len(output.getvalue().encode("utf-8")) <= 32768


def test_failed_thumbnail_http_response_is_closed(provider, monkeypatch):
    from yt_dlp.networking import Response
    from yt_dlp.networking.exceptions import HTTPError
    body = io.BytesIO(b"forbidden")
    response = Response(body, "https://i1.sndcdn.com/secret", {}, status=403)
    original = link_import._open_https

    def fail_image(request, *, budget, media=None):
        if media:
            raise HTTPError(response)
        return original(request, budget=budget)

    monkeypatch.setattr(link_import, "_open_https", fail_image)
    result = provider.run()
    assert result.artwork_path is None
    assert body.closed


@pytest.mark.parametrize("bad", [False, "outside", "oversize", "filename"])
def test_parent_validates_worker_artwork_containment_and_limit(tmp_path, monkeypatch, bad):
    cache = tmp_path / "cache"
    cache.mkdir()
    audio = cache / "audio.m4a"
    audio.write_bytes(b"audio")
    artwork = (tmp_path if bad == "outside" else cache) / ("unsafe.svg" if bad == "filename" else "thumbnail.png")
    artwork.write_bytes(PNG if bad != "oversize" else PNG + b"x" * (4 * 1024 * 1024))
    message = {"type": "result", "path": str(audio), "name": "audio.m4a", "metadata": {}, "artwork_path": str(artwork)}
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO((json.dumps(message) + "\n").encode()),
                              returncode=0, poll=lambda: 0)
    monkeypatch.setattr(link_import.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(link_import, "_ProcessContainment", lambda p: SimpleNamespace(close=lambda: None))
    if bad:
        with pytest.raises(link_import.LinkImportError, match="artwork"):
            link_import.download_link("https://soundcloud.com/artist/track", cache, 8 * 1024 * 1024)
    else:
        result = link_import.download_link("https://soundcloud.com/artist/track", cache, 8 * 1024 * 1024)
        assert result.artwork_path == artwork
