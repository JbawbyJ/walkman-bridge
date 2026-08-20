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

# One sink for everything a windowed exe would otherwise lose: the logging
# module, uvicorn's stdout logging, and stray prints all share this handle so
# their writes interleave instead of overwriting each other.
_LOG_SINK = None


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
    """Route logging AND the std streams to one shared log-file handle.

    PyInstaller's windowed build sets sys.stdout/stderr to None; uvicorn's
    default config attaches a StreamHandler to sys.stdout, which would kill
    the server thread on its first log line. And two separately-seeked handles
    on the same file overwrite each other — hence the single shared sink.
    """
    global _LOG_SINK
    try:
        _LOG_SINK = open(log_path(), "w", buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        _LOG_SINK = open(os.devnull, "w")

    handler = logging.StreamHandler(_LOG_SINK)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    if sys.stdout is None:
        sys.stdout = _LOG_SINK
    if sys.stderr is None:
        sys.stderr = _LOG_SINK


def acquire_app_mutex() -> None:
    """Create the named mutex the installer's AppMutex directive checks, so
    installing/uninstalling while the app runs gets Inno's proper warning.
    The handle stays open for the process lifetime by design."""
    try:
        import ctypes

        ctypes.windll.kernel32.CreateMutexW(None, False, "WalkmanBridgeAppMutex")
    except Exception:
        pass  # non-Windows dev run


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


def wait_for_transfer_finish() -> None:
    """Never exit while the JSymphonic JVM is rewriting the device database.

    The backend modules are importable here because main() put the backend dir
    on sys.path. Transcoding-only work is not device-critical; the shim lock
    covers exactly the dangerous window.
    """
    try:
        import jsymphonic as jsy
    except Exception:
        return
    if jsy.wait_for_idle(timeout=0.05):
        return
    warn(
        "A transfer is still writing to the Walkman.\n\n"
        f"{APP_NAME} will close as soon as it finishes.\n"
        "Do NOT unplug the device yet."
    )
    if not jsy.wait_for_idle(timeout=1800):
        logging.error("shim still busy after 30 minutes; exiting anyway")


def main() -> int:
    configure_logging()
    acquire_app_mutex()
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

        webview.create_window(
            APP_NAME, url, width=1180, height=820, min_size=(900, 640)
        )
        webview.start()  # blocks until the window is closed
        logging.info("window closed, shutting down")
    except Exception:
        # WebView2 missing or pywebview failed: still usable, just in a browser.
        logging.exception("native window failed; falling back to the browser")
        import webbrowser

        webbrowser.open(url)
        # The dialog is the only stop control in this mode — loop until the
        # user actually wants to stop instead of killing the server on the
        # first accidental dismiss.
        while not ask_yes_no(
            f"{APP_NAME} could not open its own window, so it is running in "
            f"your browser at {url}.\n\nStop {APP_NAME} now?"
        ):
            pass

    wait_for_transfer_finish()
    server.should_exit = True
    thread.join(timeout=10)
    return 0


def _message_box(text: str, caption: str, flags: int) -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(None, text, caption, flags))
    except Exception:
        print(f"{caption}: {text}", file=sys.stderr)
        return 0


def ask_yes_no(text: str) -> bool:
    IDYES = 6
    result = _message_box(text, APP_NAME, 0x04 | 0x20)  # MB_YESNO | MB_ICONQUESTION
    if result == 0:  # message box unavailable (non-Windows dev run)
        return True
    return result == IDYES


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
    except KeyboardInterrupt:
        return 0
    except SystemExit as e:  # a deliberate exit is not a crash
        return e.code if isinstance(e.code, int) else 0
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
