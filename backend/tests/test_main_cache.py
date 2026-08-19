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
