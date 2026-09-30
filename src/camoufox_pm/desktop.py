"""Desktop mode: run the server in the background and show a native window.

Requires the optional ``desktop`` extra (pywebview). Used by ``camoufox-pm
--desktop`` and by the packaged desktop app.
"""

import contextlib
import os
import socket
import threading
import time

import uvicorn
from loguru import logger

# How long a window close waits for the server's shutdown: browsers opened in
# the session are closed and their profile leases released in that window.
SHUTDOWN_TIMEOUT = 30.0


def _wait_until_serving(host: str, port: int, timeout: float = 30.0) -> bool:
    """Block until the server accepts connections, or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=1):
            return True
        time.sleep(0.2)
    return False


def _ensure_port_free(host: str, port: int) -> None:
    """Refuse to start when the port is already taken, and say so.

    Without this the server thread fails to bind while the readiness probe
    below connects to *someone else's* open socket and takes it for a healthy
    server — the user then gets a window aimed at a stranger's application.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: claim the port for real
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise SystemExit(
            f"Cannot start: {host}:{port} is already in use ({exc.strerror}). "
            "Another copy of Fingerprint Lite may be running, or another "
            "program holds that port. Close it, or start with --port <other>."
        ) from exc
    finally:
        probe.close()


def run_desktop(
    host: str = "127.0.0.1",
    port: int = 8000,
    title: str = "Fingerprint Lite",
    storage_path: str | None = None,
) -> None:
    """Start the API server in a thread and open a native desktop window.

    ``storage_path`` keeps the window engine's own cache (WebView2) where the
    app keeps the rest of its files, instead of the user profile on C:.
    """
    _ensure_port_free(host, port)
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SystemExit(
            "Desktop mode needs the 'desktop' extra. Install it with:\n"
            "  uv sync --extra desktop      # or\n"
            "  pip install 'camoufox-profile-manager[desktop]'"
        ) from exc

    # Import the app object (not an import string) so this works inside a frozen
    # PyInstaller bundle where uvicorn cannot resolve the module by name.
    from camoufox_pm.main import app

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_until_serving(host, port):
        server.should_exit = True
        raise SystemExit(f"Server did not start on {host}:{port}")

    logger.info("Desktop server ready: pid={} port={}", os.getpid(), port)
    ui_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    webview.create_window(title, f"http://{ui_host}:{port}/", width=1280, height=800)
    logger.info("Desktop GUI loop starting: pid={}", os.getpid())
    if storage_path is not None:
        webview.start(storage_path=storage_path)
    else:
        webview.start()
    logger.info("Desktop GUI loop ended: pid={}", os.getpid())

    # The window was closed — stop the server and wait for it to finish. The
    # server runs in a daemon thread, so returning here would end the process
    # mid-shutdown: browsers opened during the session are cut off, and a
    # profile lease outlives the process that took it, blocking the profile
    # for a full CPM_LEASE_TTL on the next start.
    server.should_exit = True
    thread.join(timeout=SHUTDOWN_TIMEOUT)
    if thread.is_alive():
        logger.warning(f"The server did not shut down within {SHUTDOWN_TIMEOUT:.0f}s; exiting.")
