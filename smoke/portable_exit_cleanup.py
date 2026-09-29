"""Window close: this session's browsers go, other browsers stay.

The portable app must end cleanly when its window closes — the backend stops
and the browsers *it* launched are closed with it — while leaving unrelated
browser processes alone. Drives the real running instance:

1. launch one profile browser through the app (its child, PID from the API),
2. start one unrelated Camoufox browser directly (not the app's business),
3. close the app window with WM_CLOSE,
4. assert: the app exited, its browser is gone, the unrelated one is alive.

    python smoke/portable_exit_cleanup.py <base_url> <profile_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from ctypes import WINFUNCTYPE, byref, create_unicode_buffer, windll, wintypes

import psutil

WINDOW_TITLE = "Fingerprint Lite"
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


def call(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


async def unrelated_browser(user_data_dir: str) -> int:
    """A Camoufox the app knows nothing about; closing the app must spare it."""
    import os

    from camoufox import AsyncCamoufox

    before = {c.pid for c in psutil.Process(os.getpid()).children(recursive=True)}
    camoufox = AsyncCamoufox(persistent_context=True, headless=True, user_data_dir=user_data_dir)
    await camoufox.start()
    time.sleep(3)
    after = [c for c in psutil.Process(os.getpid()).children(recursive=True) if c.pid not in before]
    assert after, "the unrelated browser process was not found"
    return after[0].pid


def main() -> int:
    base = sys.argv[1].rstrip("/")
    profile_id = sys.argv[2]
    scratch = sys.argv[3] if len(sys.argv) > 3 else "C:\\Windows\\Temp\\fpl-unrelated-browser"

    print("[1] launching a browser through the app")
    launched = call(base, "POST", f"/profiles/{profile_id}/launch", {"headless": True})
    print(f"    launch status: {launched.get('status')}")

    hwnd = find_window(WINDOW_TITLE)
    assert hwnd, "the app window is not there"
    pid_buf = wintypes.DWORD()
    windll.user32.GetWindowThreadProcessId(hwnd, byref(pid_buf))
    app_pid = pid_buf.value
    print(f"    app pid: {app_pid}")

    time.sleep(8)  # let the browser settle under the app
    app = psutil.Process(app_pid)
    children = [c.pid for c in app.children(recursive=True)]
    print(f"    the app's child processes: {children}")
    assert children, "the launched browser should be a child of the app"

    print("[2] starting an unrelated browser")
    unrelated_pid = asyncio.run(unrelated_browser(scratch))
    assert unrelated_pid, "could not determine the unrelated browser pid"
    print(f"    unrelated browser pid: {unrelated_pid}")
    assert unrelated_pid not in children

    print("[3] closing the app window")
    windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    print("[4] verifying who survived")
    deadline = time.time() + 30
    while time.time() < deadline and psutil.pid_exists(app_pid):
        time.sleep(0.5)
    assert not psutil.pid_exists(app_pid), "the app did not exit after its window closed"
    time.sleep(2)
    survivors = [pid for pid in children if psutil.pid_exists(pid)]
    assert not survivors, f"the app's children outlived it: {survivors}"
    assert psutil.pid_exists(unrelated_pid), "the unrelated browser was killed too"
    print(f"    app {app_pid}: exited; children {children}: all gone (correct)")
    print(f"    unrelated browser {unrelated_pid}: alive (correct)")

    print("[5] cleaning up the unrelated browser")
    try:
        psutil.Process(unrelated_pid).kill()
    except psutil.NoSuchProcess:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
