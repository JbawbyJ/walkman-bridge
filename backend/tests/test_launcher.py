"""Unit tests for the cheap, deterministic parts of packaging/launcher.py."""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parents[2] / "packaging"
if str(PACKAGING_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGING_DIR))

import launcher  # noqa: E402


def test_free_port_is_bindable():
    port = launcher.free_port()
    assert 1024 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))  # would raise if the port were held


def test_wait_for_server_true_when_listening():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert launcher.wait_for_server(port, timeout=5.0) is True


def test_wait_for_server_false_when_nothing_there():
    port = launcher.free_port()
    assert launcher.wait_for_server(port, timeout=1.0) is False


def test_install_root_source_checkout():
    # Not frozen in tests: root is the repo checkout containing packaging/.
    root = launcher.install_root()
    assert (root / "packaging" / "launcher.py").is_file()


def test_configure_bundled_runtimes_sets_env_and_backend(tmp_path, monkeypatch):
    monkeypatch.delenv("WALKMAN_BRIDGE_JAVA", raising=False)
    monkeypatch.delenv("WALKMAN_BRIDGE_FFMPEG", raising=False)

    (tmp_path / "jre" / "bin").mkdir(parents=True)
    (tmp_path / "jre" / "bin" / "java.exe").write_bytes(b"")
    (tmp_path / "ffmpeg").mkdir()
    (tmp_path / "ffmpeg" / "ffmpeg.exe").write_bytes(b"")
    (tmp_path / "app" / "backend").mkdir(parents=True)

    backend = launcher.configure_bundled_runtimes(tmp_path)

    import os
    assert os.environ["WALKMAN_BRIDGE_JAVA"].endswith("java.exe")
    assert os.environ["WALKMAN_BRIDGE_FFMPEG"].endswith("ffmpeg.exe")
    assert backend == tmp_path / "app" / "backend"


def test_configure_bundled_runtimes_source_layout(tmp_path, monkeypatch):
    """No bundled runtimes, no app/ dir: env untouched, backend/ resolved."""
    monkeypatch.delenv("WALKMAN_BRIDGE_JAVA", raising=False)
    monkeypatch.delenv("WALKMAN_BRIDGE_FFMPEG", raising=False)
    (tmp_path / "backend").mkdir()

    backend = launcher.configure_bundled_runtimes(tmp_path)

    import os
    assert "WALKMAN_BRIDGE_JAVA" not in os.environ
    assert "WALKMAN_BRIDGE_FFMPEG" not in os.environ
    assert backend == tmp_path / "backend"


def test_wait_for_idle_reflects_shim_lock():
    """The launcher's exit gate: busy shim -> False, idle -> True."""
    import jsymphonic

    assert jsymphonic.wait_for_idle(timeout=0.05) is True

    jsymphonic._shim_lock.acquire()
    try:
        assert jsymphonic.wait_for_idle(timeout=0.05) is False
    finally:
        jsymphonic._shim_lock.release()

    # Released mid-wait: wait_for_idle unblocks.
    jsymphonic._shim_lock.acquire()
    timer = threading.Timer(0.2, jsymphonic._shim_lock.release)
    timer.start()
    try:
        assert jsymphonic.wait_for_idle(timeout=5.0) is True
    finally:
        timer.cancel()
