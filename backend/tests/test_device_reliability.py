import shutil
import os

import pytest

import device


def test_same_mount_directory_replacement_rejected(tmp_path):
    mount = tmp_path / "mount"
    (mount / "OMGAUDIO").mkdir(parents=True)
    identity = device.capture_device_identity(mount)
    mount.rename(tmp_path / "old-volume")
    (mount / "OMGAUDIO").mkdir(parents=True)
    with pytest.raises(device.DeviceChangedError):
        device.revalidate_device(identity)


def test_backup_failure_never_publishes_completed_directory(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "OMGAUDIO").mkdir(parents=True)
    backups = tmp_path / "backups"

    def fail(src, dest, **kwargs):
        dest.mkdir()
        (dest / "incomplete").write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", fail)
    with pytest.raises(device.BackupError, match="disk full"):
        device.backup_device(mount, backups)
    assert all(path.name.endswith(".partial") for path in backups.iterdir())


def test_corrupt_copy_never_publishes_completed_directory(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "OMGAUDIO").mkdir(parents=True)
    (mount / "OMGAUDIO" / "song.OMA").write_bytes(b"original")
    backups = tmp_path / "backups"
    copytree = shutil.copytree

    def corrupt(src, dest, **kwargs):
        monkeypatch.setattr(shutil, "copytree", copytree)
        copytree(src, dest, **kwargs)
        (dest / "OMGAUDIO" / "song.OMA").write_bytes(b"tampered")

    monkeypatch.setattr(shutil, "copytree", corrupt)
    with pytest.raises(device.BackupError, match="verif"):
        device.backup_device(mount, backups)
    assert all(path.name.endswith(".partial") for path in backups.iterdir())


def test_verified_backup_publishes_exact_tree(tmp_path):
    mount = tmp_path / "mount"
    (mount / "OMGAUDIO" / "empty").mkdir(parents=True)
    (mount / "OMGAUDIO" / "song.OMA").write_bytes(b"original")
    backups = tmp_path / "backups"
    result = device.backup_device(mount, backups)
    assert not result.name.endswith(".partial")
    assert (result / "OMGAUDIO" / "song.OMA").read_bytes() == b"original"
    assert (result / "OMGAUDIO" / "empty").is_dir()
    assert list(backups.iterdir()) == [result]


def test_source_changes_during_backup_prevent_publication(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "OMGAUDIO").mkdir(parents=True)
    source = mount / "OMGAUDIO" / "song.OMA"
    source.write_bytes(b"original")
    copytree = shutil.copytree

    def change_source(src, dest, **kwargs):
        monkeypatch.setattr(shutil, "copytree", copytree)
        copytree(src, dest, **kwargs)
        source.write_bytes(b"new contents")

    monkeypatch.setattr(shutil, "copytree", change_source)
    backups = tmp_path / "backups"
    with pytest.raises(device.BackupError, match="verification"):
        device.backup_device(mount, backups)
    assert all(path.name.endswith(".partial") for path in backups.iterdir())


def test_unreadable_source_is_not_silently_omitted(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    (mount / "OMGAUDIO").mkdir(parents=True)
    scandir = os.scandir

    def denied(path):
        if str(path) == str(mount / "OMGAUDIO"):
            raise PermissionError("cannot read device subtree")
        return scandir(path)

    monkeypatch.setattr(os, "scandir", denied)
    with pytest.raises(device.BackupError, match="cannot read device subtree"):
        device.backup_device(mount, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()
