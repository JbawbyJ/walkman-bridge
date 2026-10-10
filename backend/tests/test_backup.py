"""POST /api/backup — dated local copy of the mock/detected mount.

Never writes the Walkman. Tests use MOCK_DEVICE_PATH only.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

import main
import device as device_module

def client(tmp_path):
    app = main.create_app(product='bridge', data_dir=tmp_path / 'state', token='backup-test', origin='http://127.0.0.1:8123')
    return TestClient(app, base_url='http://127.0.0.1:8123', headers={'X-NightOps-Token': 'backup-test'})


DATE_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")


def _tree_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            out[rel] = "dir"
    return out


def test_backup_copies_mock_device_including_omgaudio(monkeypatch, tmp_path):
    device = tmp_path / "device"
    omg = device / "OMGAUDIO" / "10F00"
    omg.mkdir(parents=True)
    (omg / "track.OMA").write_bytes(b"oma-bytes")
    (device / "readme.txt").write_text("hello", encoding="utf-8")

    backups = tmp_path / "backups"
    monkeypatch.setenv("MOCK_DEVICE_PATH", str(device))
    monkeypatch.setenv("WALKMAN_BACKUP_DIR", str(backups))

    with client(tmp_path) as c:
        r = c.post("/api/backup")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    dest = Path(body["path"])
    assert dest.is_dir()
    assert dest.parent == tmp_path / 'state' / 'backups'
    assert re.match(r'^\d{4}-\d{2}-\d{2}-', dest.name)
    assert (dest / "OMGAUDIO" / "10F00" / "track.OMA").read_bytes() == b"oma-bytes"
    assert (dest / "readme.txt").read_text(encoding="utf-8") == "hello"


def test_backup_does_not_write_the_walkman(monkeypatch, tmp_path):
    device = tmp_path / "device"
    omg = device / "OMGAUDIO" / "10F00"
    omg.mkdir(parents=True)
    (omg / "track.OMA").write_bytes(b"oma-bytes")
    (device / "readme.txt").write_text("hello", encoding="utf-8")
    before = _tree_hashes(device)

    backups = tmp_path / "backups"
    monkeypatch.setenv("MOCK_DEVICE_PATH", str(device))
    monkeypatch.setenv("WALKMAN_BACKUP_DIR", str(backups))

    with client(tmp_path) as c:
        r = c.post("/api/backup")

    assert r.status_code == 200, r.text
    dest = Path(r.json()["path"])
    assert dest.is_dir()
    assert dest.resolve() != device.resolve()
    try:
        dest.resolve().relative_to(device.resolve())
        on_device = True
    except ValueError:
        on_device = False
    assert on_device is False
    assert _tree_hashes(device) == before


def test_backup_copies_detected_mount(monkeypatch, tmp_path):
    device = tmp_path / "mount"
    (device / "OMGAUDIO").mkdir(parents=True)
    (device / "OMGAUDIO" / "x.OMA").write_bytes(b"x")
    backups = tmp_path / "backups"
    monkeypatch.delenv("MOCK_DEVICE_PATH", raising=False)
    monkeypatch.setattr(device_module, "find_walkman", lambda: device)
    monkeypatch.setenv("WALKMAN_BACKUP_DIR", str(backups))

    with client(tmp_path) as c:
        r = c.post("/api/backup")

    assert r.status_code == 200, r.text
    dest = Path(r.json()["path"])
    assert (dest / "OMGAUDIO" / "x.OMA").read_bytes() == b"x"
    assert dest.parent == tmp_path / 'state' / 'backups'


def test_backup_404_when_no_device(monkeypatch, tmp_path):
    # Never scan real drives — a live Walkman must not be touched.
    monkeypatch.delenv("MOCK_DEVICE_PATH", raising=False)
    monkeypatch.setattr(device_module, "find_walkman", lambda: None)

    with client(tmp_path) as c:
        r = c.post("/api/backup")

    assert r.status_code == 404
    assert "Walkman" in r.json()["detail"]


def test_backup_refuses_destination_on_the_device(monkeypatch, tmp_path):
    device = tmp_path / "device"
    (device / "OMGAUDIO").mkdir(parents=True)
    (device / "OMGAUDIO" / "keep.bin").write_bytes(b"stay")
    before = _tree_hashes(device)

    monkeypatch.setenv("MOCK_DEVICE_PATH", str(device))
    monkeypatch.setenv("WALKMAN_BACKUP_DIR", str(device / "backups"))
    original_backup = device_module.backup_device
    monkeypatch.setattr(device_module, "backup_device", lambda mount, dest: original_backup(mount, device / "backups"))

    with client(tmp_path) as c:
        r = c.post("/api/backup")

    assert r.status_code == 409
    assert "Walkman" in r.json()["detail"]
    assert _tree_hashes(device) == before
    assert not (device / "backups").exists()

