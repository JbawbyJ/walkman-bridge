"""Provider cover art for link imports: bounded fetch, own Defender scan, then a forced-JPEG re-encode."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import link_import
import media_service
import scan
import transcode
from media_store import MediaError, MediaStore
from test_api import Scanner, context  # noqa: F401  (pytest fixture)
from test_link_import import _FakeResponse, _FakeYoutubeDL, _install_fake_yt_dlp

VIDEO = "dQw4w9WgXcQ"
COVER = b"\xff\xd8\xff\xe0" + b"cover bytes" + b"\xff\xd9"


class _Image:
    def __init__(self, data=COVER, content_type="image/jpeg", length=True):
        self.headers = {"Content-Type": content_type} if content_type else {}
        if length:
            self.headers["Content-Length"] = str(len(data))
        self._data = io.BytesIO(data)
        self.closed = False

    def read(self, size=-1):
        return self._data.read(size)

    def close(self):
        self.closed = True


class _Missing(Exception):
    def __init__(self, status=404):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.response = _Image()


def _serve(monkeypatch, routes):
    """Audio through the fake yt-dlp; cover URLs answered from routes in request order."""
    _install_fake_yt_dlp(monkeypatch)
    served = []

    def fake_open(request, *, budget):
        _FakeYoutubeDL.network_urls.append(request.url)
        link_import._validate_outbound_url(request.url)
        if "googlevideo" in request.url:
            return _FakeResponse()
        answer = routes.get(request.url)
        if answer is None:
            raise _Missing(404)
        if isinstance(answer, BaseException):
            raise answer
        served.append(answer)
        return answer

    monkeypatch.setattr(link_import, "_open_https", fake_open)
    return served


def _youtube(monkeypatch, tmp_path, routes, **result):
    served = _serve(monkeypatch, routes)
    monkeypatch.setattr(_FakeYoutubeDL, "result", {**_FakeYoutubeDL.result, "id": VIDEO, **result})
    folder = tmp_path / "workspace"
    folder.mkdir()
    media = link_import._download_link_inprocess(f"https://youtu.be/{VIDEO}", folder, max_bytes=16)
    return media, folder, served


def _cover_urls():
    return [url for url in _FakeYoutubeDL.network_urls if "googlevideo" not in url]


def test_youtube_cover_is_fetched_after_the_audio_from_the_validated_id(monkeypatch, tmp_path):
    maxres = f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg"
    media, folder, served = _youtube(monkeypatch, tmp_path, {maxres: _Image()})
    assert media.path == folder / "audio.m4a" and media.path.read_bytes() == b"data"
    assert _FakeYoutubeDL.network_urls == ["https://r1---sn.test.googlevideo.com/videoplayback", maxres]
    assert (folder / link_import.COVER_NAME).read_bytes() == COVER
    assert served[0].closed
    if os.name != "nt":
        assert (folder / link_import.COVER_NAME).stat().st_mode & 0o777 == 0o600
    options = _FakeYoutubeDL.last_options
    assert "writethumbnail" not in options and options["postprocessors"] == [] and options["fixup"] == "never"
    assert set(vars(media)) == {"path", "name", "metadata"}


def test_only_missing_covers_fall_through_to_the_next_candidate(monkeypatch, tmp_path):
    hq = f"https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg"
    gone = _Missing(410)
    routes = {f"https://i.ytimg.com/vi/{VIDEO}/sddefault.jpg": gone, hq: _Image()}
    _, folder, _ = _youtube(monkeypatch, tmp_path, routes)
    assert _cover_urls() == [f"https://i.ytimg.com/vi/{VIDEO}/{name}" for name in link_import._YOUTUBE_COVERS]
    assert gone.response.closed
    assert (folder / link_import.COVER_NAME).read_bytes() == COVER


@pytest.mark.parametrize("failure", [
    link_import.LinkImportError("The provider made too many network requests."),
    _Missing(503),
    OSError("connection reset"),
])
def test_other_cover_failures_stop_without_failing_the_audio(monkeypatch, tmp_path, failure):
    routes = {f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg": failure}
    media, folder, _ = _youtube(monkeypatch, tmp_path, routes)
    assert media.path.read_bytes() == b"data"
    assert len(_cover_urls()) == 1
    assert not (folder / link_import.COVER_NAME).exists()


@pytest.mark.parametrize("response", [
    _Image(b"\x89PNG\r\n\x1a\n" + b"x" * 16, content_type=None),
    _Image(b"RIFF\x10\x00\x00\x00WEBPVP8 ", content_type=None),
    _Image(COVER, content_type="text/html"),
    _Image(COVER[:-2]),
    _Image(b"\xff\xd8\xff\xd9"),
    _Image(b"\xff\xd8\xff" + b"x" * link_import.COVER_MAX_BYTES + b"\xff\xd9", length=False),
    _Image(b"\xff\xd8\xff" + b"x" * link_import.COVER_MAX_BYTES + b"\xff\xd9"),
])
def test_cover_bytes_are_compared_never_decoded_and_bad_ones_are_dropped(monkeypatch, tmp_path, response):
    media, folder, _ = _youtube(monkeypatch, tmp_path, {f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg": response})
    assert media.path.read_bytes() == b"data"
    assert not (folder / link_import.COVER_NAME).exists()
    assert len(_cover_urls()) == 1


def test_no_cover_request_for_another_video_or_a_spent_deadline(monkeypatch, tmp_path):
    _youtube(monkeypatch, tmp_path, {}, id="abc")
    assert _cover_urls() == []
    monkeypatch.setattr(link_import, "MAX_SECONDS", link_import._COVER_MIN_REMAINING - 1)
    other = tmp_path / "second"
    other.mkdir()
    _youtube(monkeypatch, other, {})
    assert _cover_urls() == []


def test_existing_cover_file_is_never_overwritten(monkeypatch, tmp_path):
    _serve(monkeypatch, {f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg": _Image()})
    monkeypatch.setattr(_FakeYoutubeDL, "result", {**_FakeYoutubeDL.result, "id": VIDEO})
    folder = tmp_path / "workspace"
    folder.mkdir()
    (folder / link_import.COVER_NAME).write_bytes(b"planted")
    media = link_import._download_link_inprocess(f"https://youtu.be/{VIDEO}", folder, max_bytes=16)
    assert media.path.read_bytes() == b"data"
    assert (folder / link_import.COVER_NAME).read_bytes() == b"planted"


def test_soundcloud_uses_only_the_pinned_t500_jpeg(monkeypatch, tmp_path):
    good = "https://i1.sndcdn.com/artworks-Ab_c-123-t500x500.jpg"
    thumbnails = [
        {"id": "original", "url": "https://i1.sndcdn.com/artworks-Ab_c-123-original.png"},
        {"id": "t500x500", "url": "https://i1.sndcdn.com/avatars-xyz-t500x500.jpg"},
        {"id": "t500x500", "url": "https://a1.sndcdn.com/artworks-xyz-t500x500.jpg"},
        {"id": "t500x500", "url": good},
        "not a thumbnail",
    ]
    _serve(monkeypatch, {good: _Image()})
    monkeypatch.setattr(_FakeYoutubeDL, "result", {**_FakeYoutubeDL.result, "thumbnails": thumbnails})
    folder = tmp_path / "workspace"
    folder.mkdir()
    link_import._download_link_inprocess("https://soundcloud.com/artist/song", folder, max_bytes=16)
    assert _cover_urls() == [good]
    assert (folder / link_import.COVER_NAME).read_bytes() == COVER


# --------------------------------------------------------------------------- #
# Managed store and scan gate
# --------------------------------------------------------------------------- #

def _store_with_item(tmp_path):
    store = MediaStore(tmp_path / "state.db", tmp_path / "cache")
    record = store.import_file("song.mp3", io.BytesIO(b"ID3 audio"))
    folder = store.root / ("download-" + "a" * 32)
    folder.mkdir()
    return store, record["id"], folder


def test_import_artifact_copies_opaque_bytes_under_a_fixed_name(tmp_path):
    store, media_id, folder = _store_with_item(tmp_path)
    (folder / "cover.jpg").write_bytes(COVER)
    record = store.import_artifact(media_id, "thumbnail", folder / "cover.jpg", 1024, crop="letterbox")
    entry = record["artifacts"]["thumbnail"]
    assert entry == dict(name="thumbnail.jpg", size_bytes=len(COVER), sha256=hashlib.sha256(COVER).hexdigest(),
                         scan=None, crop="letterbox")
    assert (store.root / media_id / "thumbnail.jpg").read_bytes() == COVER
    with pytest.raises(MediaError):
        store.import_artifact(media_id, "thumbnail", folder / "cover.jpg", 1024)
    assert (store.root / media_id / "thumbnail.jpg").read_bytes() == COVER


@pytest.mark.parametrize("make", ["tiny", "large", "directory", "hardlink", "symlink"])
def test_import_artifact_refuses_anything_but_one_bounded_regular_file(tmp_path, make):
    store, media_id, folder = _store_with_item(tmp_path)
    source = folder / "cover.jpg"
    if make == "tiny":
        source.write_bytes(b"\xff\xd8")
    elif make == "large":
        source.write_bytes(b"x" * 2048)
    elif make == "directory":
        source.mkdir()
    elif make == "hardlink":
        source.write_bytes(COVER)
        os.link(source, folder / "second-name")
    else:
        target = folder / "elsewhere.jpg"
        target.write_bytes(COVER)
        try:
            source.symlink_to(target)
        except OSError:
            pytest.skip("symlinks unavailable")
    with pytest.raises((MediaError, OSError)):
        store.import_artifact(media_id, "thumbnail", source, 1024)
    assert "thumbnail" not in store.get(media_id)["artifacts"]
    assert not (store.root / media_id / "thumbnail.jpg").exists()


def test_startup_removes_an_unreferenced_thumbnail(tmp_path):
    store, media_id, _ = _store_with_item(tmp_path)
    orphan = store.root / media_id / "thumbnail.jpg"
    orphan.write_bytes(COVER)
    MediaStore(tmp_path / "state.db", tmp_path / "cache")
    assert not orphan.exists()


def test_artwork_url_needs_the_artwork_entry_not_just_the_status(tmp_path):
    store, media_id, _ = _store_with_item(tmp_path)
    record = store.update(media_id, artwork_status="ready")
    assert "artwork_url" not in store.public(record)


@pytest.mark.parametrize("name, data, accepted", [
    ("thumbnail.jpg", COVER, True),
    ("artwork.jpg", COVER, True),
    ("cover.jpg", COVER, False),
    ("thumbnail.png", COVER, False),
    ("thumbnail.jpg", b"\x89PNG\r\n\x1a\n\x00\x00", False),
    ("thumbnail.jpg", b"\xff\xd8\xff" + b"x" * (1024 * 1024) + b"\xff\xd9", False),
])
def test_jpeg_scan_gate_names_both_managed_images(tmp_path, name, data, accepted):
    path = tmp_path / name
    path.write_bytes(data)
    assert (scan._validate_artwork_format(path, len(data)) is None) is accepted


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #

def test_cover_conversion_forces_the_jpeg_decoder_and_removes_failed_output(tmp_path, monkeypatch):
    source, output = tmp_path / "thumbnail.jpg", tmp_path / "artwork.jpg"
    source.write_bytes(COVER)
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        output.write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 69, stderr="Invalid data found when processing input")

    monkeypatch.setattr(transcode, "_ensure_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(transcode, "_run_bounded", run)
    with pytest.raises(transcode.AudioError):
        transcode.convert_cleared_image(source, output, "letterbox")
    assert not output.exists()
    cmd = seen["cmd"]
    before_input = cmd[:cmd.index("-i")]
    for flag, value in (("-f", "jpeg_pipe"), ("-format_whitelist", "jpeg_pipe"), ("-c:v", "mjpeg"),
                        ("-codec_whitelist", "mjpeg"), ("-protocol_whitelist", "file,pipe"), ("-max_pixels", "16777216")):
        assert before_input[before_input.index(flag) + 1] == value
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[cmd.index("-map_metadata") + 1] == "-1"
    assert "min(ih,trunc(iw*9/16/2)*2)" in cmd[cmd.index("-vf") + 1]
    assert seen["kwargs"]["timeout"] <= 30
    with pytest.raises(transcode.AudioError):
        transcode.convert_cleared_image(source, output, "stretch")
    with pytest.raises(transcode.AudioError):
        transcode.convert_cleared_image(source, source)


@pytest.fixture
def real_ffmpeg():
    if os.environ.get("NIGHTOPS_REAL_MOCK") != "1":
        pytest.skip("opt-in real ffmpeg cover conversion")
    return transcode._ensure_ffmpeg()


def _render(binary, source, out):
    result = subprocess.run([binary, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                             "-frames:v", "1", str(out)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def _size(binary, path):
    result = subprocess.run([binary, "-hide_banner", "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=30)
    return result.stderr


def test_real_cover_conversion_squares_and_strips_letterbox_bars(tmp_path, real_ffmpeg):
    wide, boxed, png = tmp_path / "wide.jpg", tmp_path / "boxed.jpg", tmp_path / "png.jpg"
    _render(real_ffmpeg, "testsrc=size=1280x720", wide)
    _render(real_ffmpeg, "color=black:size=480x360,drawbox=x=0:y=45:w=480:h=270:color=red:t=fill", boxed)
    transcode.convert_cleared_image(wide, tmp_path / "a.jpg", "letterbox")
    assert b"512x512" in _size(real_ffmpeg, tmp_path / "a.jpg")
    transcode.convert_cleared_image(boxed, tmp_path / "b.jpg", "letterbox")
    assert b"270x270" in _size(real_ffmpeg, tmp_path / "b.jpg")
    top = subprocess.run([real_ffmpeg, "-v", "error", "-i", str(tmp_path / "b.jpg"), "-vf", "crop=iw:2:0:0,scale=1:1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=30).stdout
    assert top[0] > 200 and top[1] < 40  # red, not a black bar
    _render(real_ffmpeg, "testsrc=size=64x64", tmp_path / "real.png")
    png.write_bytes((tmp_path / "real.png").read_bytes())
    with pytest.raises(transcode.AudioError):
        transcode.convert_cleared_image(png, tmp_path / "c.jpg")
    assert not (tmp_path / "c.jpg").exists()


# --------------------------------------------------------------------------- #
# API: link import end to end
# --------------------------------------------------------------------------- #

class _ImageScanner(Scanner):
    """Records which gate ran on which file; image verdicts can be set per file name."""

    def __init__(self):
        super().__init__()
        self.log = []
        self.image_verdicts = {}

    def scan_audio(self, path):
        self.log.append(("audio", path.name))
        return super().scan_audio(path)

    def scan_artwork(self, path):
        self.log.append(("image", path.name))
        verdict = self.image_verdicts.get(path.name)
        if verdict is None:
            return super().scan_audio(path)
        result = dict(verdict, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
        return SimpleNamespace(to_dict=lambda: result)


THREAT = dict(ok=False, state="THREAT", reason="Threat found", reason_code="defender_threat", defender_status="threat")
GENERATED = b"\xff\xd8\xff\xdbgenerated artwork\xff\xd9"


@pytest.fixture
def cover_context(context, monkeypatch):
    scanner = _ImageScanner()
    context.app.state.service.scanner = scanner
    context.scanner = scanner
    events = []

    def download(url, directory, max_bytes, progress=None):
        path = directory / "audio.m4a"
        path.write_bytes(b"fake downloaded audio")
        if events and events[0] == "with-cover":
            (directory / link_import.COVER_NAME).write_bytes(COVER)
        return SimpleNamespace(path=path, name="Remote song.m4a",
                               metadata={"title": "Remote song", "provider": "youtube"})

    def convert(src, out, crop="square"):
        service = context.app.state.service
        record = context.app.state.store.get(out.parent.name)
        assert scanner.clearance_valid(src, record["artifacts"]["thumbnail"]["scan"]), "decoded before clearance"
        events.append(("convert", src.name, crop))
        out.write_bytes(GENERATED)
        return out

    monkeypatch.setattr(link_import, "download_link", download)
    monkeypatch.setattr(media_service, "convert_cleared_image", convert)
    monkeypatch.setattr(media_service, "extract_cleared_artwork", lambda *a, **k: pytest.fail("no embedded art here"))
    context.events = events
    return context


def _import_link(ctx):
    response = ctx.client.post("/api/media/import-link", json={"url": f"https://www.youtube.com/watch?v={VIDEO}"})
    assert response.status_code == 200, response.text
    job = ctx.client.get("/api/jobs/" + response.json()["job_id"]).json()
    return job, job["files"][0]["media_id"]


def test_link_import_cover_is_scanned_then_converted_then_scanned_again(cover_context):
    ctx = cover_context
    ctx.events.append("with-cover")
    job, media_id = _import_link(ctx)
    assert job["status"] == "done", job
    record = ctx.app.state.store.get(media_id)
    assert set(record["artifacts"]) == {"source", "thumbnail", "artwork"}
    assert record["artifacts"]["thumbnail"]["crop"] == "letterbox"
    assert ctx.scanner.log == [("audio", "source.m4a"), ("image", "thumbnail.jpg"), ("image", "artwork.jpg")]
    assert ctx.events[1:] == [("convert", "thumbnail.jpg", "letterbox")]
    assert record["status"] == "ready" and record["scan"]["reason_code"] == "clean"
    item = ctx.client.get("/api/queue").json()["items"][0]
    assert item["artwork_url"] == f"/api/media/{media_id}/artwork"
    served = ctx.client.get(item["artwork_url"])
    assert served.status_code == 200 and served.content == GENERATED
    assert not list(ctx.app.state.store.root.glob("download-*"))
    assert not any("cover art was not kept" in line for line in job["log_tail"])


def test_threat_on_the_provider_cover_never_decodes_it_and_keeps_the_audio(cover_context):
    ctx = cover_context
    ctx.events.append("with-cover")
    ctx.scanner.image_verdicts["thumbnail.jpg"] = THREAT
    job, media_id = _import_link(ctx)
    assert job["status"] == "done", job
    assert job["files"][0]["state"] == "ready"
    record = ctx.app.state.store.get(media_id)
    assert record["status"] == "ready" and record["scan"]["reason_code"] == "clean"
    assert record["artwork_status"] == "failed"
    assert "thumbnail" not in record["artifacts"] and "artwork" not in record["artifacts"]
    assert not (ctx.app.state.store.root / media_id / "thumbnail.jpg").exists()
    assert ctx.events[1:] == []
    assert "artwork_url" not in ctx.client.get("/api/queue").json()["items"][0]


def test_clean_provider_cover_alone_never_marks_art_ready(cover_context):
    ctx = cover_context
    ctx.events.append("with-cover")
    ctx.scanner.image_verdicts["artwork.jpg"] = dict(ok=False, state="ERROR", reason="Defender unavailable",
                                                     reason_code="defender_unavailable", defender_status="unavailable")
    job, media_id = _import_link(ctx)
    assert job["status"] == "done"
    record = ctx.app.state.store.get(media_id)
    assert record["artifacts"]["thumbnail"]["scan"]["ok"] is True
    assert record["artwork_status"] == "failed"
    assert "artwork_url" not in ctx.client.get("/api/queue").json()["items"][0]


def test_link_import_without_a_cover_stays_quiet(cover_context):
    job, media_id = _import_link(cover_context)
    assert job["status"] == "done"
    record = cover_context.app.state.store.get(media_id)
    assert set(record["artifacts"]) == {"source"} and record.get("artwork_status") is None
    assert cover_context.scanner.log == [("audio", "source.m4a")]
    assert not any("cover art was not kept" in line for line in job["log_tail"])


def test_embedded_art_wins_over_the_provider_cover(cover_context, monkeypatch):
    ctx = cover_context
    ctx.events.append("with-cover")
    monkeypatch.setattr(media_service, "analyze_cleared_audio", lambda path: {"has_artwork": True})

    def extract(src, out, *args, **kwargs):
        ctx.events.append(("extract", src.name))
        out.write_bytes(GENERATED)
        return out

    monkeypatch.setattr(media_service, "extract_cleared_artwork", extract)
    job, media_id = _import_link(ctx)
    assert job["status"] == "done"
    assert ctx.events[1:] == [("extract", "source.m4a")]
    assert ("image", "thumbnail.jpg") not in ctx.scanner.log
    assert ctx.app.state.store.get(media_id)["artwork_status"] == "ready"


def test_rescan_re_clears_provider_artwork(cover_context):
    ctx = cover_context
    ctx.events.append("with-cover")
    _, media_id = _import_link(ctx)
    store = ctx.app.state.store
    record = store.get(media_id)
    record["artifacts"]["artwork"]["scan"] = None
    store.update(media_id, artifacts=record["artifacts"], artwork_status="needs_scan")
    ctx.scanner.log.clear()
    service = ctx.app.state.service
    service.prepare_artwork(media_id)
    assert store.get(media_id)["artwork_status"] == "ready"
    assert ("image", "artwork.jpg") in ctx.scanner.log
    assert len([event for event in ctx.events if event[0:1] == ("convert",)]) == 1
