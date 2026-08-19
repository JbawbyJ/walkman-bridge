"""ffmpeg resolution: bundled binary (installed app) vs PATH (dev checkout)."""
from __future__ import annotations

import pytest

import transcode
from transcode import AudioError, _ensure_ffmpeg


def test_env_override_wins_over_path(tmp_path, monkeypatch):
    bundled = tmp_path / "ffmpeg.exe"
    bundled.write_text("")
    monkeypatch.setenv("WALKMAN_BRIDGE_FFMPEG", str(bundled))
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "C:\\somewhere\\ffmpeg.exe")

    assert _ensure_ffmpeg() == str(bundled)


def test_falls_back_to_path_when_env_unset(monkeypatch):
    monkeypatch.delenv("WALKMAN_BRIDGE_FFMPEG", raising=False)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "C:\\onpath\\ffmpeg.exe")

    assert _ensure_ffmpeg() == "C:\\onpath\\ffmpeg.exe"


def test_missing_env_target_is_an_error_not_a_silent_fallback(tmp_path, monkeypatch):
    """A wrong bundled path must fail loudly — silently using some other ffmpeg
    would make an installed app's behavior depend on the host machine."""
    monkeypatch.setenv("WALKMAN_BRIDGE_FFMPEG", str(tmp_path / "nope.exe"))
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "C:\\onpath\\ffmpeg.exe")

    with pytest.raises(AudioError, match="WALKMAN_BRIDGE_FFMPEG"):
        _ensure_ffmpeg()


def test_no_ffmpeg_anywhere_raises(monkeypatch):
    monkeypatch.delenv("WALKMAN_BRIDGE_FFMPEG", raising=False)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: None)

    with pytest.raises(AudioError, match="ffmpeg not found"):
        _ensure_ffmpeg()
