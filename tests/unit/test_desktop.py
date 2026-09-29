"""Desktop startup: a taken port is reported, and a close waits for the server.

Without the port check, the server thread fails to bind while the readiness
probe finds *someone else's* open socket and cheerfully opens a window at it.
Without the shutdown wait, closing the window ends the process mid-shutdown:
browsers are cut off and a profile lease outlives the process that took it.
"""

import socket
import sys
import time
import types

import pytest

from camoufox_pm import desktop


class FakeWebview(types.ModuleType):
    """Stands in for pywebview; records any window it is asked to open."""

    def __init__(self):
        super().__init__("webview")
        self.windows: list[tuple] = []

    def create_window(self, title, url, **kwargs):
        self.windows.append((title, url))

    def start(self):
        raise AssertionError("the window must not start when the port is taken")


def test_a_taken_port_is_reported_before_any_window_opens(monkeypatch):
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.listen(1)
    try:
        with pytest.raises(SystemExit) as excinfo:
            desktop.run_desktop(host="127.0.0.1", port=port)
    finally:
        probe.close()

    assert str(port) in str(excinfo.value)
    assert "in use" in str(excinfo.value)
    assert fake.windows == []


class RecordingWebview(types.ModuleType):
    """Stands in for pywebview and just records the calls."""

    def __init__(self):
        super().__init__("webview")
        self.calls: dict = {}

    def create_window(self, title, url, **kwargs):
        self.calls["window"] = {"title": title, "url": url, **kwargs}

    def start(self, **kwargs):
        self.calls["start"] = kwargs


class FakeServer:
    """A server whose shutdown takes a moment."""

    instances: list = []

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.finished = False
        FakeServer.instances.append(self)

    def run(self) -> None:
        while not self.should_exit:
            time.sleep(0.01)
        time.sleep(0.3)  # a shutdown that is not instant
        self.finished = True


def test_closing_the_window_waits_for_the_server_shutdown(tmp_path, monkeypatch):
    """A shutdown cut short leaks the profile lease past the process."""
    FakeServer.instances.clear()
    webview = RecordingWebview()

    monkeypatch.setattr(desktop, "_ensure_port_free", lambda host, port: None)
    monkeypatch.setattr(desktop, "_wait_until_serving", lambda host, port, timeout=30.0: True)
    monkeypatch.setattr(desktop.uvicorn, "Config", lambda app, **kwargs: None)
    monkeypatch.setattr(desktop.uvicorn, "Server", FakeServer)
    monkeypatch.setitem(sys.modules, "webview", webview)

    desktop.run_desktop(host="127.0.0.1", port=8123, storage_path=str(tmp_path / "webview"))

    server = FakeServer.instances[-1]
    assert webview.calls["window"]["title"] == "Fingerprint Lite"
    assert webview.calls["start"]["storage_path"] == str(tmp_path / "webview")
    assert server.should_exit is True
    assert server.finished is True, "closing the window must wait for the shutdown to finish"
