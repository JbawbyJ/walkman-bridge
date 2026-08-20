"""
main.py — track-list cache and batch upload, via TestClient with the
jsymphonic layer monkeypatched out (no JVM, no device).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from jobs import job_store


@pytest.fixture(autouse=True)
def fresh_cache():
    main.track_cache.invalidate()
    yield
    main.track_cache.invalidate()


@pytest.fixture
def client(monkeypatch, tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    monkeypatch.setattr(main, "find_walkman", lambda: tmp_path)

    calls = {"list": 0}

    def fake_list(mount):
        calls["list"] += 1
        return [{"id": "1", "title": "T", "artist": "A", "album": "L",
                 "duration_seconds": 9}]

    monkeypatch.setattr(main, "list_tracks", fake_list)
    monkeypatch.setattr(main, "remove_track", lambda mount, track_id: None)

    with TestClient(main.app) as c:
        yield c, calls


def test_cache_serves_repeated_gets(client):
    c, calls = client
    for _ in range(3):
        r = c.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["id"] == "1"
    assert calls["list"] == 1


def test_refresh_param_forces_refetch(client):
    c, calls = client
    c.get("/api/tracks")
    c.get("/api/tracks?refresh=1")
    assert calls["list"] == 2
    c.get("/api/tracks")
    assert calls["list"] == 2  # back to serving the cache


def test_delete_invalidates_cache(client):
    c, calls = client
    c.get("/api/tracks")
    assert calls["list"] == 1
    r = c.delete("/api/tracks/1")
    assert r.status_code == 200
    c.get("/api/tracks")
    assert calls["list"] == 2


def test_upload_batches_one_add_call(client, monkeypatch):
    c, calls = client

    added: list[list] = []

    def fake_add(mount, files, on_event):
        added.append(list(files))
        on_event({"event": "start", "files": len(files)})
        on_event({"event": "file", "step": "transfer", "name": files[0].name})
        on_event({"event": "progress", "step": "transfer", "percent": 100.0})
        on_event({"event": "done"})

    monkeypatch.setattr(main, "add_tracks", fake_add)
    monkeypatch.setattr(main, "normalize_to_mp3", lambda src: src)

    c.get("/api/tracks")
    assert calls["list"] == 1

    r = c.post("/api/upload", files=[
        ("files", ("a.mp3", b"aaa", "audio/mpeg")),
        ("files", ("b.mp3", b"bbb", "audio/mpeg")),
    ])
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # TestClient runs the background task before returning, so the job is done.
    job = job_store.get(job_id)
    assert job is not None
    assert job.status.value == "done"
    assert job.progress == 1.0

    # One shim invocation for the whole batch.
    assert len(added) == 1
    assert sorted(p.name for p in added[0]) == ["a.mp3", "b.mp3"]

    # A successful add invalidates the cache: next poll re-reads the device.
    c.get("/api/tracks")
    assert calls["list"] == 2


def test_upload_destination_strips_paths_and_dedupes(tmp_path):
    first = main.upload_destination(tmp_path, r"C:\Music\Album\01 Intro.mp3", 0)
    first.write_bytes(b"one")
    second = main.upload_destination(tmp_path, "Mixtape/01 Intro.mp3", 1)
    second.write_bytes(b"two")
    escaped = main.upload_destination(tmp_path, "../escape.mp3", 2)
    dotted = main.upload_destination(tmp_path, "..", 3)

    assert first.name == "01 Intro.mp3"
    assert second.name == "01 Intro-1.mp3"
    assert escaped.name == "escape.mp3"
    assert dotted.name == "upload-3"
    assert first.parent == second.parent == escaped.parent == dotted.parent == tmp_path
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"


def test_upload_keeps_same_named_files(client, monkeypatch):
    c, _calls = client
    added_names: list[str] = []
    added_bytes: dict[str, bytes] = {}

    def fake_add(mount, files, on_event):
        added_names.extend(p.name for p in files)
        added_bytes.update({p.name: p.read_bytes() for p in files})
        on_event({"event": "done"})

    monkeypatch.setattr(main, "add_tracks", fake_add)
    monkeypatch.setattr(main, "normalize_to_mp3", lambda src: src)

    r = c.post("/api/upload", files=[
        ("files", ("01.mp3", b"aaa", "audio/mpeg")),
        ("files", ("01.mp3", b"bbb", "audio/mpeg")),
    ])
    assert r.status_code == 200
    job = job_store.get(r.json()["job_id"])
    assert job is not None
    assert job.status.value == "done"
    assert sorted(added_names) == ["01-1.mp3", "01.mp3"]
    assert added_bytes["01.mp3"] == b"aaa"
    assert added_bytes["01-1.mp3"] == b"bbb"


def test_upload_partial_transcode_is_not_all_success(client, monkeypatch):
    c, calls = client
    added: list[list] = []

    def fake_norm(src):
        if src.name.startswith("bad"):
            raise main.AudioError("corrupt")
        return src

    def fake_add(mount, files, on_event):
        added.append(list(files))
        on_event({"event": "done"})

    monkeypatch.setattr(main, "normalize_to_mp3", fake_norm)
    monkeypatch.setattr(main, "add_tracks", fake_add)

    c.get("/api/tracks")
    assert calls["list"] == 1

    r = c.post("/api/upload", files=[
        ("files", ("good.mp3", b"aaa", "audio/mpeg")),
        ("files", ("bad.mp3", b"xxx", "audio/mpeg")),
    ])
    job = job_store.get(r.json()["job_id"])
    assert job is not None
    assert job.status.value == "partial"
    assert "1 of 2" in job.message
    assert len(added) == 1
    assert [p.name for p in added[0]] == ["good.mp3"]
    # Survivors were written — next poll must re-read the device.
    c.get("/api/tracks")
    assert calls["list"] == 2
