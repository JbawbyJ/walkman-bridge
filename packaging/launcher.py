"""
Walkman Bridge desktop launcher.

Frozen with PyInstaller into WalkmanBridge.exe. Starts the FastAPI server on a
free loopback port, then shows the dashboard in a native window (WebView2 via
pywebview) so the app behaves like an installed program: taskbar entry, own
icon, no browser tab, no console window.

Layout when installed (see packaging/build.ps1):

    Walkman Bridge/
      WalkmanBridge.exe   <- this
      jre/bin/java.exe
      ffmpeg/ffmpeg.exe
      app/backend/...     <- FastAPI app + vendor/jsymphonic.jar
      app/frontend/dist/  <- built dashboard, served by the backend

Running from a source checkout (python packaging/launcher.py) works too: paths
resolve relative to the repo and the bundled runtimes are simply absent, so the
app falls back to whatever is on PATH.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
from pathlib import Path

APP_NAME = "Walkman Bridge"


def install_root() -> Path:
    """Directory holding the runtimes: the exe's folder when frozen, else repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def log_path() -> Path:
    directory = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Walkman Bridge"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "walkman-bridge.log"


def configure_logging() -> None:
    # A windowed exe has no console, so a log file is the only way a user can
    # tell us what went wrong.
    logging.basicConfig(
        filename=str(log_path()),
        filemode="w",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_std_streams()


def _ensure_std_streams() -> None:
    """Give the process real stdout/stderr.

    PyInstaller's windowed build sets both to None. uvicorn's default logging
    config attaches a StreamHandler to sys.stdout, so with None it kills the
    server thread on its first log line — the app then hangs waiting for a
    server that already died. Pointing the streams at the log file fixes that
    and captures uvicorn's own output for support.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        sink = open(log_path(), "a", buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        sink = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def configure_bundled_runtimes(root: Path) -> Path:
    """Point the backend at the runtimes we ship, and return the backend dir."""
    java = root / "jre" / "bin" / "java.exe"
    if java.is_file():
        os.environ["WALKMAN_BRIDGE_JAVA"] = str(java)
        logging.info("using bundled java: %s", java)

    ffmpeg = root / "ffmpeg" / "ffmpeg.exe"
    if ffmpeg.is_file():
        os.environ["WALKMAN_BRIDGE_FFMPEG"] = str(ffmpeg)
        logging.info("using bundled ffmpeg: %s", ffmpeg)

    backend = root / "app" / "backend"
    if not backend.is_dir():  # source checkout
        backend = root / "backend"
    return backend


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """Poll the port until uvicorn accepts connections (or we give up)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def main() -> int:
    configure_logging()
    root = install_root()
    backend_dir = configure_bundled_runtimes(root)
    logging.info("root=%s backend=%s", root, backend_dir)

    if not (backend_dir / "main.py").is_file():
        fail(f"Installation looks incomplete: {backend_dir}\\main.py is missing.")
        return 1

    # The backend imports its modules by bare name (device, jobs, ...), so it
    # must be importable as a top-level package directory.
    sys.path.insert(0, str(backend_dir))
    os.chdir(backend_dir)

    port = free_port()
    url = f"http://127.0.0.1:{port}"

    import uvicorn

    from main import app as fastapi_app

    server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="info")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_for_server(port):
        fail("The Walkman Bridge server did not start.\n\nSee: " + str(log_path()))
        return 1
    logging.info("server up at %s", url)

    try:
        import webview  # pywebview -> WebView2 on Windows

        window = webview.create_window(
            APP_NAME, url, width=1180, height=820, min_size=(900, 640)
        )
        webview.start()  # blocks until the window is closed
        logging.info("window closed, shutting down")
    except Exception:
        # WebView2 missing or pywebview failed: still usable, just in a browser.
        logging.exception("native window failed; falling back to the browser")
        import webbrowser

        webbrowser.open(url)
        warn(
            f"{APP_NAME} could not open its own window, so it opened in your "
            f"browser instead.\n\nClose this message to stop the app."
        )

    server.should_exit = True
    thread.join(timeout=10)
    return 0


def _message_box(text: str, caption: str, flags: int) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, caption, flags)
    except Exception:
        print(f"{caption}: {text}", file=sys.stderr)


def fail(text: str) -> None:
    logging.error(text)
    _message_box(text, APP_NAME, 0x10)  # MB_ICONERROR


def warn(text: str) -> None:
    logging.warning(text)
    _message_box(text, APP_NAME, 0x30)  # MB_ICONWARNING


def guarded_main() -> int:
    """A windowed exe has no console: an unhandled exception would vanish."""
    try:
        return main()
    except BaseException:
        import traceback

        detail = traceback.format_exc()
        try:
            logging.error("fatal: %s", detail)
        except Exception:
            pass
        fail(
            f"{APP_NAME} could not start.\n\n"
            f"{detail.strip().splitlines()[-1] if detail.strip() else ''}\n\n"
            f"Details: {log_path()}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(guarded_main())
