"""Windowed portable edition: the double-click lifecycle, end to end.

Drives the real frozen build from a fresh folder:

1. start ``FingerprintLite.exe`` (windowed) and wait for the server,
2. check the ``Data`` layout, the instance lock and the captured console log,
3. ask the console entry about the running instance (shared single-instance
   detection), and check ``browser status``,
4. close the window with ``WM_CLOSE`` and verify a clean exit: process gone,
   port free, instance lock released.

This is instrumentation only — the artifact itself needs no Python.

    python smoke/portable_window_lifecycle.py <portable-folder> [port]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from ctypes import WINFUNCTYPE, create_unicode_buffer, windll, wintypes
from pathlib import Path

WINDOW_TITLE = "Fingerprint Lite"

# EnumWindows wants a C callback type; Python 3.12 ships no WNDENUMPROC alias.
ENUMPROC = WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window(title: str) -> int:
    found: list[int] = []

    def callback(hwnd, _lparam):
        length = windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = create_unicode_buffer(length + 1)
            windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                found.append(hwnd)
        return True

    windll.user32.EnumWindows(ENUMPROC(callback), 0)
    return found[0] if found else 0


def wait_for_window(title: str, timeout: float = 30.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window(title)
        if hwnd:
            return hwnd
        time.sleep(0.3)
    raise SystemExit(f"the window {title!r} never appeared")


def wait_for_health(port: int, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                return json.loads(response.read())
        except Exception:
            time.sleep(0.5)
    raise SystemExit(f"the server on port {port} never became healthy")


def port_free(port: int) -> bool:
    import socket

    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> int:
    folder = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8125
    gui = folder / "FingerprintLite.exe"
    cli = folder / "camoufox-pm.exe"
    if not gui.exists() or not cli.exists():
        raise SystemExit(f"not a portable folder: {folder}")

    print(f"[1] starting {gui.name} on port {port}")
    # A fresh folder means a first start: answer the wizard the way a user with
    # the default choices would, through the explicit automation door.
    answers_file = folder / "wizard-answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "data_dir": str(folder / "Data"),
                "browser_dir": str(folder / "Browser"),
                "temp_dir": str(folder / "Temp"),
                "browser_source": "skip",
            }
        ),
        encoding="utf-8",
    )
    app = subprocess.Popen(
        [str(gui), "--port", str(port), "--wizard-answers", str(answers_file)],
        cwd=folder,
    )
    try:
        health = wait_for_health(port)
        print(f"    health: {health}")
        assert health["status"] == "healthy", health

        data = folder / "Data"
        assert (data / "profiles").is_dir(), "Data/profiles missing"
        assert (data / "logs").is_dir(), "Data/logs missing"
        assert (data / "config.env").is_file(), "Data/config.env missing"
        lock = json.loads((data / "instance.lock").read_text(encoding="utf-8"))
        assert lock["pid"] == app.pid, lock
        console_log = data / "logs" / "console.log"
        assert console_log.is_file(), "the windowed entry must capture its output"
        print(f"[2] Data layout, instance lock (pid {app.pid}) and console.log verified")

        hwnd = wait_for_window(WINDOW_TITLE)
        print(f"[3] window found (hwnd {hwnd})")

        print("[4] shared single-instance detection via the console entry")
        second = subprocess.run(
            [str(cli), "--port", str(port), "--no-browser"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert second.returncode == 0, (second.returncode, second.stderr)
        assert "already running" in second.stderr, second.stderr
        print("    second start refused with a clear message (exit 0)")

        print("[5] browser status from the console entry")
        status = subprocess.run(
            [str(cli), "browser", "status"], cwd=folder, capture_output=True, text=True, timeout=60
        )
        assert status.returncode == 0, status.stderr
        print("   ", status.stdout.strip().replace("\n", " | "))

        print("[6] closing the window (WM_CLOSE)")
        windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        try:
            code = app.wait(timeout=30)
        except subprocess.TimeoutExpired:
            app.kill()
            raise SystemExit("the app did not exit after its window was closed") from None
        print(f"    exited cleanly with code {code}")

        deadline = time.time() + 10
        while time.time() < deadline and not port_free(port):
            time.sleep(0.3)
        assert port_free(port), "the port is still held after exit"
        assert not (data / "instance.lock").exists(), "the instance lock outlived the run"
        assert app.poll() is not None
        print("[7] port released and instance lock removed — clean exit confirmed")
    finally:
        if app.poll() is None:
            app.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
