"""Provider cover art for link imports: bounded fetch, own Defender scan, then a forced-JPEG re-encode."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import threading
import time
from contextlib import contextmanager
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
    _Image(b"\x00\xd8\xff" + b"cover bytes" + b"\xff\xd9"),
    _Image(b"\xff\xd8\xff" + b"x" * link_import.COVER_MAX_BYTES + b"\xff\xd9", length=False),
    _Image(b"\xff\xd8\xff" + b"x" * link_import.COVER_MAX_BYTES + b"\xff\xd9"),
])
def test_cover_bytes_are_compared_never_decoded_and_bad_ones_are_dropped(monkeypatch, tmp_path, response):
    media, folder, _ = _youtube(monkeypatch, tmp_path, {f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg": response})
    assert media.path.read_bytes() == b"data"
    assert not (folder / link_import.COVER_NAME).exists()
    assert len(_cover_urls()) == 1


class _Endless(_Image):
    """A JPEG-looking body with no Content-Length that never ends."""

    def __init__(self):
        super().__init__(length=False)
        self.served = 0

    def read(self, size=-1):
        size = 64 * 1024 if size is None or size < 0 else size
        chunk = (b"\xff\xd8\xff" + b"x" * size)[:size] if not self.served else b"x" * size
        self.served += size
        return chunk


def test_cover_read_stops_at_the_cap(monkeypatch, tmp_path):
    endless = _Endless()
    _, folder, _ = _youtube(monkeypatch, tmp_path, {f"https://i.ytimg.com/vi/{VIDEO}/maxresdefault.jpg": endless})
    assert endless.served <= link_import.COVER_MAX_BYTES + 1
    assert not (folder / link_import.COVER_NAME).exists()


def _timed_transport(monkeypatch, cover):
    """Audio through the fake yt-dlp; every cover request handled by cover(url, budget)."""
    _install_fake_yt_dlp(monkeypatch)
    seen = []

    def fake_open(request, *, budget):
        seen.append((request.url, budget, budget.deadline - time.monotonic()))
        _FakeYoutubeDL.network_urls.append(request.url)
        if "googlevideo" in request.url:
            return _FakeResponse()
        return cover(request.url, budget)

    monkeypatch.setattr(link_import, "_open_https", fake_open)
    monkeypatch.setattr(_FakeYoutubeDL, "result", {**_FakeYoutubeDL.result, "id": VIDEO})
    return seen


def test_cover_uses_the_audio_budget_inside_a_short_window(monkeypatch, tmp_path):
    seen = _timed_transport(monkeypatch, lambda url, budget: _Image())
    link_import._download_link_inprocess(f"https://youtu.be/{VIDEO}", tmp_path, max_bytes=16)
    (_, audio_budget, audio_left), (_, cover_budget, cover_left) = seen
    assert cover_budget is audio_budget
    assert audio_left > link_import._COVER_SECONDS and 0 < cover_left <= link_import._COVER_SECONDS


def test_a_stalled_cover_request_is_abandoned_at_the_window(monkeypatch, tmp_path):
    release = threading.Event()
    returned = threading.Event()

    def stalled(url, budget):
        release.wait(10)  # a DNS lookup, connect or read that does not return
        returned.set()
        return _Image()

    _timed_transport(monkeypatch, stalled)
    monkeypatch.setattr(link_import, "_COVER_SECONDS", 0.3)
    started = time.monotonic()
    try:
        media = link_import._download_link_inprocess(f"https://youtu.be/{VIDEO}", tmp_path, max_bytes=16)
        assert time.monotonic() - started < 3
        assert media.path.read_bytes() == b"data"
        assert not (tmp_path / link_import.COVER_NAME).exists()
    finally:
        release.set()
    assert returned.wait(5)
    time.sleep(0.1)
    assert not (tmp_path / link_import.COVER_NAME).exists()  # only the caller writes the file


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


# Explicit ids: pytest would otherwise put the 1 MiB value in the test id, which
# Windows cannot hold in PYTEST_CURRENT_TEST and the Actions log cannot print.
@pytest.mark.parametrize("name, data, accepted", [
    pytest.param("thumbnail.jpg", COVER, True, id="thumbnail"),
    pytest.param("artwork.jpg", COVER, True, id="artwork"),
    pytest.param("cover.jpg", COVER, False, id="unmanaged-name"),
    pytest.param("thumbnail.png", COVER, False, id="png-name"),
    pytest.param("thumbnail.jpg", b"\x89PNG\r\n\x1a\n\x00\x00", False, id="png-bytes"),
    pytest.param("thumbnail.jpg", b"\xff\xd8\xff" + b"x" * (1024 * 1024) + b"\xff\xd9", False, id="over-1-mib"),
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


@pytest.mark.parametrize("written", [b"", b"\xff\xd8" + b"x" * 2048 + b"\xff\xd9", b"\xff\xd8not a complete jpeg"],
                         ids=["empty", "over-limit", "truncated"])
def test_cover_conversion_rejects_bad_output_and_removes_it(tmp_path, monkeypatch, written):
    source, output = tmp_path / "thumbnail.jpg", tmp_path / "artwork.jpg"
    source.write_bytes(COVER)

    def run(cmd, **kwargs):
        output.write_bytes(written)
        return subprocess.CompletedProcess(cmd, 0, stderr="")

    monkeypatch.setattr(transcode, "_ensure_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(transcode, "_run_bounded", run)
    with pytest.raises(transcode.AudioError):
        transcode.convert_cleared_image(source, output, max_bytes=1024)
    assert not output.exists()


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
    """Link import with a fake worker; ctx.cover is the cover.jpg it leaves, ctx.provider its provider."""
    scanner = _ImageScanner()
    context.app.state.service.scanner = scanner
    context.scanner = scanner
    context.cover, context.provider, context.convert_failures, context.events = None, "youtube", 0, []
    held = []
    real_locked_read = media_service.locked_read

    @contextmanager
    def tracking_locked_read(path, root):
        with real_locked_read(path, root) as stream:
            held.append(Path(path).name)
            try:
                yield stream
            finally:
                held.remove(Path(path).name)

    def download(url, directory, max_bytes, progress=None):
        path = directory / "audio.m4a"
        path.write_bytes(b"fake downloaded audio")
        if context.cover is not None:
            (directory / link_import.COVER_NAME).write_bytes(context.cover)
        return SimpleNamespace(path=path, name="Remote song.m4a",
                               metadata={"title": "Remote song", "provider": context.provider})

    def convert(src, out, crop="square"):
        record = context.app.state.store.get(out.parent.name)
        assert scanner.clearance_valid(src, record["artifacts"]["thumbnail"]["scan"]), "decoded before clearance"
        assert "thumbnail.jpg" in held, "decoded without holding the cover's read lock"
        assert record.get("artwork_status") != "ready", "art marked ready before artwork.jpg exists"
        context.events.append(("convert", src.name, crop))
        if context.convert_failures:
            context.convert_failures -= 1
            raise transcode.AudioError("Provider artwork could not be decoded")
        out.write_bytes(GENERATED)
        return out

    monkeypatch.setattr(media_service, "locked_read", tracking_locked_read)
    monkeypatch.setattr(link_import, "download_link", download)
    monkeypatch.setattr(media_service, "convert_cleared_image", convert)
    monkeypatch.setattr(media_service, "extract_cleared_artwork", lambda *a, **k: pytest.fail("no embedded art here"))
    return context


def _import_link(ctx, url=f"https://www.youtube.com/watch?v={VIDEO}"):
    response = ctx.client.post("/api/media/import-link", json={"url": url})
    assert response.status_code == 200, response.text
    job = ctx.client.get("/api/jobs/" + response.json()["job_id"]).json()
    return job, job["files"][0]["media_id"]


def _source_intact(ctx, media_id):
    record = ctx.app.state.store.get(media_id)
    source = record["artifacts"]["source"]
    assert not source.get("purged")
    assert (ctx.app.state.store.root / media_id / source["name"]).read_bytes() == b"fake downloaded audio"
    assert record["status"] == "ready" and record["scan"]["reason_code"] == "clean"
    assert record["scan"]["sha256"] == hashlib.sha256(b"fake downloaded audio").hexdigest()
    assert ctx.client.get(f"/api/media/{media_id}/stream", headers={"Range": "bytes=0-3"}).status_code == 206


def test_link_import_cover_is_scanned_then_converted_then_scanned_again(cover_context):
    ctx = cover_context
    ctx.cover = COVER
    job, media_id = _import_link(ctx)
    assert job["status"] == "done", job
    record = ctx.app.state.store.get(media_id)
    assert set(record["artifacts"]) == {"source", "thumbnail", "artwork"}
    assert record["artifacts"]["thumbnail"]["crop"] == "letterbox"
    assert ctx.scanner.log == [("audio", "source.m4a"), ("image", "thumbnail.jpg"), ("image", "artwork.jpg")]
    assert ctx.events == [("convert", "thumbnail.jpg", "letterbox")]
    _source_intact(ctx, media_id)
    item = ctx.client.get("/api/queue").json()["items"][0]
    assert item["artwork_url"] == f"/api/media/{media_id}/artwork"
    served = ctx.client.get(item["artwork_url"])
    assert served.status_code == 200 and served.content == GENERATED
    assert not list(ctx.app.state.store.root.glob("download-*"))
    assert not any("cover art was not kept" in line for line in job["log_tail"])


def test_soundcloud_covers_are_squared_without_the_letterbox_crop(cover_context):
    ctx = cover_context
    ctx.cover, ctx.provider = COVER, "soundcloud"
    job, media_id = _import_link(ctx, "https://soundcloud.com/artist/song")
    assert job["status"] == "done", job
    assert ctx.app.state.store.get(media_id)["artifacts"]["thumbnail"]["crop"] == "square"
    assert ctx.events == [("convert", "thumbnail.jpg", "square")]


def test_threat_on_the_provider_cover_never_decodes_it_and_keeps_the_audio(cover_context):
    ctx = cover_context
    ctx.cover = COVER
    ctx.scanner.image_verdicts["thumbnail.jpg"] = THREAT
    job, media_id = _import_link(ctx)
    assert job["status"] == "done", job
    assert job["files"][0]["state"] == "ready"
    _source_intact(ctx, media_id)
    record = ctx.app.state.store.get(media_id)
    assert record["artwork_status"] == "failed"
    assert "thumbnail" not in record["artifacts"] and "artwork" not in record["artifacts"]
    assert not (ctx.app.state.store.root / media_id / "thumbnail.jpg").exists()
    assert ctx.events == []
    assert "artwork_url" not in ctx.client.get("/api/queue").json()["items"][0]


def test_threat_on_the_provider_cover_refuses_artwork_made_from_it(cover_context):
    ctx = cover_context
    ctx.cover = COVER
    _, media_id = _import_link(ctx)
    store, service = ctx.app.state.store, ctx.app.state.service
    path = store.root / media_id / "thumbnail.jpg"
    verdict = dict(THREAT, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    with pytest.raises(MediaError):
        service.accept_scan(media_id, "thumbnail", verdict)
    record = store.get(media_id)
    assert record["artifacts"]["thumbnail"]["purged"] and record["artifacts"]["artwork"]["purged"]
    assert not record["artifacts"]["source"].get("purged")
    assert "artwork_url" not in store.public(record)


@pytest.mark.parametrize("bad", ["oversized", "hardlink"])
def test_a_bad_cover_from_the_worker_never_fails_the_audio(cover_context, monkeypatch, bad):
    ctx = cover_context
    if bad == "oversized":
        ctx.cover = b"\xff\xd8\xff" + b"x" * link_import.COVER_MAX_BYTES + b"\xff\xd9"
    else:
        ctx.cover = COVER
        real = link_import.download_link

        def linked(url, directory, max_bytes, progress=None):
            media = real(url, directory, max_bytes, progress)
            os.link(directory / link_import.COVER_NAME, directory / "second-name.jpg")
            return media

        monkeypatch.setattr(link_import, "download_link", linked)
    job, media_id = _import_link(ctx)
    assert job["status"] == "done", job
    assert "thumbnail" not in ctx.app.state.store.get(media_id)["artifacts"]
    assert any("cover art was not kept" in line for line in job["log_tail"])
    _source_intact(ctx, media_id)


def test_clean_provider_cover_alone_never_marks_art_ready(cover_context):
    ctx = cover_context
    ctx.cover = COVER
    ctx.scanner.image_verdicts["artwork.jpg"] = dict(ok=False, state="ERROR", reason="Defender unavailable",
                                                     reason_code="defender_unavailable", defender_status="unavailable")
    job, media_id = _import_link(ctx)
    assert job["status"] == "done"
    record = ctx.app.state.store.get(media_id)
    assert record["artifacts"]["thumbnail"]["scan"]["ok"] is True
    assert record["artwork_status"] == "failed"
    assert "artwork_url" not in ctx.client.get("/api/queue").json()["items"][0]


def test_a_retry_reuses_the_cover_clearance_without_touching_the_audio_verdict(cover_context):
    ctx = cover_context
    ctx.cover, ctx.convert_failures = COVER, 1
    _, media_id = _import_link(ctx)
    store = ctx.app.state.store
    assert store.get(media_id)["artwork_status"] == "failed"
    ctx.scanner.log.clear()
    ctx.app.state.service.prepare_artwork(media_id)
    record = store.get(media_id)
    assert record["artwork_status"] == "ready"
    assert ctx.scanner.log == [("image", "artwork.jpg")]  # the source's and the cover's clearances still hold
    assert len(ctx.events) == 2
    _source_intact(ctx, media_id)


def test_cover_elevation_only_reports_through_the_artwork_status(cover_context, monkeypatch):
    ctx = cover_context
    ctx.cover = COVER
    ctx.scanner.image_verdicts["thumbnail.jpg"] = dict(ok=False, state="AWAITING_PERMISSION", reason="Elevation needed",
                                                       reason_code="defender_elevation_required",
                                                       defender_status="elevation_required")
    requested, statuses = [], []
    store = ctx.app.state.store
    real_update = store.update

    def update(media_id, **changes):
        statuses.append(changes)
        return real_update(media_id, **changes)

    bridge = ctx.app.state.service.bridge
    monkeypatch.setattr(bridge, "request", lambda media_id, path, sha256, size, batch_id=None:
                        requested.append(path.name) or {"request_id": "r1"})
    monkeypatch.setattr(bridge, "wait", lambda request_id, timeout=None: None)
    monkeypatch.setattr(bridge, "cancel", lambda request_id, reason_code: dict(
        ok=False, state="ERROR", reason="Timed out", reason_code=reason_code, defender_status="unavailable"))
    store.update = update
    job, media_id = _import_link(ctx)
    assert job["status"] == "done", job
    assert requested == ["thumbnail.jpg"]
    assert {"artwork_status": "awaiting_permission"} in statuses
    assert not any(change.get("status") == "awaiting_permission" for change in statuses)
    assert store.get(media_id)["artwork_status"] == "failed"
    assert ctx.events == []
    _source_intact(ctx, media_id)


def test_link_import_without_a_cover_stays_quiet(cover_context):
    job, media_id = _import_link(cover_context)
    assert job["status"] == "done"
    record = cover_context.app.state.store.get(media_id)
    assert set(record["artifacts"]) == {"source"} and record.get("artwork_status") is None
    assert cover_context.scanner.log == [("audio", "source.m4a")]
    assert not any("cover art was not kept" in line for line in job["log_tail"])


def test_embedded_art_wins_over_the_provider_cover(cover_context, monkeypatch):
    ctx = cover_context
    ctx.cover = COVER
    monkeypatch.setattr(media_service, "analyze_cleared_audio", lambda path: {"has_artwork": True})

    def extract(src, out, *args, **kwargs):
        ctx.events.append(("extract", src.name))
        out.write_bytes(GENERATED)
        return out

    monkeypatch.setattr(media_service, "extract_cleared_artwork", extract)
    job, media_id = _import_link(ctx)
    assert job["status"] == "done"
    assert ctx.events == [("extract", "source.m4a")]
    assert ("image", "thumbnail.jpg") not in ctx.scanner.log
    assert ctx.app.state.store.get(media_id)["artwork_status"] == "ready"


def test_rescan_re_clears_provider_artwork(cover_context):
    ctx = cover_context
    ctx.cover = COVER
    _, media_id = _import_link(ctx)
    store = ctx.app.state.store
    record = store.get(media_id)
    record["artifacts"]["artwork"]["scan"] = None
    store.update(media_id, artifacts=record["artifacts"], artwork_status="needs_scan")
    ctx.scanner.log.clear()
    ctx.app.state.service.prepare_artwork(media_id)
    assert store.get(media_id)["artwork_status"] == "ready"
    assert ("image", "artwork.jpg") in ctx.scanner.log
    assert len(ctx.events) == 1
