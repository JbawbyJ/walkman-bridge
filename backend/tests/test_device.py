"""device.py — MOCK_DEVICE_PATH override behavior."""
from __future__ import annotations

import pytest

import device


def test_mock_device_honored(monkeypatch, tmp_path):
    (tmp_path / "OMGAUDIO").mkdir()
    monkeypatch.setenv("MOCK_DEVICE_PATH", str(tmp_path))
    monkeypatch.setattr(
        device, "_candidates", lambda: pytest.fail("scanned drives despite mock")
    )
    assert device.find_walkman() == tmp_path


def test_mock_device_without_omgaudio_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_DEVICE_PATH", str(tmp_path))  # no OMGAUDIO inside
    monkeypatch.setattr(
        device, "_candidates", lambda: pytest.fail("scanned drives despite mock")
    )
    assert device.find_walkman() is None


def test_no_mock_falls_back_to_scanning(monkeypatch, tmp_path):
    monkeypatch.delenv("MOCK_DEVICE_PATH", raising=False)
    (tmp_path / "OMGAUDIO").mkdir()
    monkeypatch.setattr(device, "_candidates", lambda: [tmp_path])
    assert device.find_walkman() == tmp_path
