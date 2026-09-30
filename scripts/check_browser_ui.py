"""Real pinned Windows browser regression, using disposable CI-only profiles.

Marionette is enabled only by this test script to inspect privileged browser UI;
normal application launches do not expose it. No Firefox profile internals are
written by this script. Search URLs are checked without sending a query online.
"""

from __future__ import annotations

import ctypes
import json
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from camoufox.addons import DefaultAddons
from camoufox.sync_api import Camoufox

from camoufox_pm import browser_env, browser_ui
from camoufox_pm.core import fingerprint_store
from camoufox_pm.core.models import Profile


class ChromeInspector:
    def __init__(self, port: int):
        deadline = time.monotonic() + 30
        while True:
            try:
                self.connection = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        self.connection.settimeout(45)
        self.request = 0
        self.receive()
        self.command("WebDriver:NewSession", {"capabilities": {}})
        self.command("Marionette:SetContext", {"value": "chrome"})

    def read(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.connection.recv(size - len(data))
            if not chunk:
                raise ConnectionError("Marionette closed before its response completed")
            data.extend(chunk)
        return bytes(data)

    def receive(self) -> Any:
        header = bytearray()
        while True:
            char = self.read(1)
            if char == b":":
                break
            header.extend(char)
            if len(header) > 12:
                raise ValueError("Invalid Marionette frame length")
        return json.loads(self.read(int(header)))

    def command(self, name: str, params: dict[str, Any]) -> Any:
        self.request += 1
        data = json.dumps([0, self.request, name, params]).encode()
        self.connection.sendall(str(len(data)).encode() + b":" + data)
        while True:
            response = self.receive()
            if isinstance(response, list) and response[0:2] == [1, self.request]:
                if response[2]:
                    raise RuntimeError(f"{name}: {response[2]}")
                return response[3]

    def script(self, code: str, *, asynchronous: bool = False) -> Any:
        name = "WebDriver:ExecuteAsyncScript" if asynchronous else "WebDriver:ExecuteScript"
        return self.command(name, {"script": code, "args": []})["value"]


SEARCH_STATE = """
const done = arguments[arguments.length - 1];
(async () => {
  await Services.search.init();
  const engines = await Services.search.getVisibleEngines();
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  done({
    engines: engines.map(e => ({name: e.name, url: e.getSubmission("privacy test 中文 &+/").uri.spec})),
    default: (await Services.search.getDefault()).name,
    privateDefault: (await Services.search.getDefaultPrivate()).name,
    highlighter: !!win.document.getElementById("cursor-highlighter"),
    nativeTitlebar: !Services.appinfo.drawInTitlebar,
    pid: Services.appinfo.processID,
    prefs: {
      titlebar: Services.prefs.getIntPref("browser.tabs.inTitlebar"),
      suggestions: Services.prefs.getBoolPref("browser.search.suggest.enabled"),
    },
    policy: Services.policies.getActivePolicies().SearchEngines,
  });
})().catch(error => done({error: String(error), stack: error.stack}));
"""


def native_caption_styles(pid: int) -> list[int]:
    """Read real Windows window styles rather than trusting the preference alone."""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    styles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd: int, _param: int) -> bool:
        process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if process.value == pid and user32.IsWindowVisible(hwnd):
            styles.append(user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF)
        return True

    user32.EnumWindows(visit, 0)
    return styles


def inspect(profile: Path, pin: dict[str, Any] | None = None, choose_startpage: bool = False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    options = Profile(name="UI regression", storage_path=str(profile)).to_camoufox_launch_options()
    options.update(headless=False, geoip=False, exclude_addons=[DefaultAddons.UBO])
    if pin is None:
        pin = fingerprint_store.resolve(options)
        assert pin, "Fingerprint must be resolved for the real browser test"
    before = json.dumps(pin, sort_keys=True)
    options["config"] = {**pin, **options["config"]}
    options["args"] = ["--marionette", "--remote-allow-system-access"]
    options["firefox_user_prefs"]["marionette.port"] = port
    with Camoufox(**options) as context:
        context.pages[0].mouse.move(300, 200)
        inspector = ChromeInspector(port)
        try:
            state = inspector.script(SEARCH_STATE, asynchronous=True)
            assert "error" not in state, state
            state["nativeWindowStyles"] = native_caption_styles(state["pid"])
            # Caption + system menu + minimise + maximise boxes are real native
            # controls, so their symbols no longer depend on spoofed font lists.
            mask = 0x00C00000 | 0x00080000 | 0x00020000 | 0x00010000
            assert any(style & mask == mask for style in state["nativeWindowStyles"]), state
            assert state["nativeTitlebar"] is True, state
            assert state["prefs"] == {"titlebar": 0, "suggestions": False}, state
            assert state["highlighter"] is False, state
            expected = {name for name, _url, _alias in browser_ui.SEARCH_ENGINES}
            assert expected <= {e["name"] for e in state["engines"]}, state
            assert all(
                e["url"].startswith("https://") for e in state["engines"] if e["name"] in expected
            )
            if choose_startpage:
                inspector.script(
                    """
                const done = arguments[arguments.length - 1];
                Services.search.setDefault(Services.search.getEngineByName("Startpage"),
                  Ci.nsISearchService.CHANGE_REASON_USER)
                  .then(() => done(true), error => done({error: String(error)}));
                """,
                    asynchronous=True,
                )
        finally:
            inspector.connection.close()
    assert json.dumps(pin, sort_keys=True) == before, "UI defaults changed a pinned fingerprint"
    return state, pin


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This acceptance check requires a Windows runner")
    root = Path(__file__).resolve().parents[1]
    browser_env.use_browser_root(root / ".work" / "browser-ui-browser")
    install = browser_env.ensure_browser()
    browser_ui.prepare_search_policy(install)
    with tempfile.TemporaryDirectory(prefix="浏览器界面-") as temporary:
        base = Path(temporary)
        first, pin = inspect(base / "profile-one", choose_startpage=True)
        assert first["default"] == first["privateDefault"] == "DuckDuckGo", first
        browser_ui.prepare_search_policy(install)
        reopened, _ = inspect(base / "profile-one", pin=pin)
        assert reopened["default"] == "Startpage", reopened
        second, _ = inspect(base / "profile-two")
        assert second["default"] == "DuckDuckGo", second
    report = {"fresh": first, "reopened": reopened, "secondProfile": second}
    destination = root / ".work" / "browser-ui-check.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    print(
        "PASS: five engines, privacy default, persisted choice, no cursor overlay, native caption controls"
    )


if __name__ == "__main__":
    main()
