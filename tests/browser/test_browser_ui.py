"""Opt-in checks of the real browser chrome, using its loopback test protocol.

Marionette is enabled only in these isolated test processes. It is never added
to production launches. The protocol is documented in Firefox Source Docs:
https://firefox-source-docs.mozilla.org/testing/marionette/Protocol.html
"""

import json
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from camoufox.addons import DefaultAddons
from camoufox.multiversion import get_active_path
from camoufox.pkgman import launch_path
from camoufox.utils import launch_options

from camoufox_pm import browser_ui
from camoufox_pm.core.models import Profile

pytestmark = pytest.mark.browser


class _ChromeClient:
    def __init__(self, port):
        deadline = time.monotonic() + 15
        while True:
            try:
                self.socket = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        self.socket.settimeout(15)
        self.request_id = 0
        self._receive()
        self.call("WebDriver:NewSession", {"capabilities": {}})
        self.call("Marionette:SetContext", {"value": "chrome"})

    def _receive(self):
        length = bytearray()
        while True:
            byte = self.socket.recv(1)
            if not byte:
                raise EOFError("Browser test connection closed")
            if byte == b":":
                break
            length.extend(byte)
        remaining = int(length)
        body = bytearray()
        while remaining:
            chunk = self.socket.recv(remaining)
            if not chunk:
                raise EOFError("Browser test response was truncated")
            body.extend(chunk)
            remaining -= len(chunk)
        return json.loads(body)

    def call(self, command, params):
        self.request_id += 1
        message = json.dumps([0, self.request_id, command, params]).encode()
        self.socket.sendall(str(len(message)).encode() + b":" + message)
        while True:
            response = self._receive()
            if isinstance(response, list) and response[1] == self.request_id:
                if response[2]:
                    raise RuntimeError(response[2])
                return response[3]

    def evaluate(self, script):
        result = self.call(
            "WebDriver:ExecuteAsyncScript",
            {
                "script": (
                    "const done = arguments[arguments.length-1];"
                    "const {SearchService: search} = ChromeUtils.importESModule("
                    "'moz-src:///toolkit/components/search/SearchService.sys.mjs');"
                    "const w = Services.wm.getMostRecentWindow('navigator:browser');"
                    f"(async () => {{ {script} }})().then(done, e => done({{error:String(e)}}));"
                ),
                "args": [],
                "scriptTimeout": 15000,
            },
        )["value"]
        assert not isinstance(result, dict) or "error" not in result, result
        return result


@contextmanager
def chrome_browser(executable: Path, profile: Path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    options = Profile(name="chrome test").to_camoufox_launch_options()
    options.pop("persistent_context")
    options.pop("user_data_dir")
    resolved = launch_options(
        **{**options, "geoip": False},
        headless=True,
        executable_path=str(executable),
        exclude_addons=[DefaultAddons.UBO],
        firefox_user_prefs={"marionette.port": port},
    )
    profile.mkdir(exist_ok=True)
    (profile / "user.js").write_text(
        "\n".join(
            f"user_pref({json.dumps(key)}, {json.dumps(value)});"
            for key, value in resolved["firefox_user_prefs"].items()
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            str(executable),
            "-headless",
            "-no-remote",
            "-profile",
            str(profile),
            "--marionette",
            "--remote-allow-system-access",
            "about:blank",
        ],
        env={**os.environ, **resolved["env"], "MOZ_MARIONETTE": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = None
    try:
        client = _ChromeClient(port)
        yield client
    finally:
        if client is not None:
            client.socket.close()
        process.terminate()
        process.wait(timeout=10)


def test_search_works_and_a_user_choice_survives_relaunch(tmp_path):
    source = get_active_path()
    assert source is not None, "Install Camoufox in the test cache before running browser tests"
    executable = browser_ui.prepare_runtime(Path(launch_path(source)), tmp_path / "runtime")
    profile = tmp_path / "profile"
    # A pre-update profile has already applied Camoufox's "None" search policy.
    # Reuse it to cover the migration, not only a brand-new profile directory.
    with chrome_browser(Path(launch_path(source)), profile) as browser:
        browser.evaluate("""
            try { await search.init(); } catch (_) {}
            Services.prefs.setStringPref('fingerprint-lite.test-marker', 'existing profile');
            Services.prefs.savePrefFile(null);
            return true;
        """)
    with chrome_browser(executable, profile) as browser:
        state = browser.evaluate("""
            await search.init();
            const engines = await search.getVisibleEngines();
            return {
                names: engines.map(e => e.name),
                default: (await search.getDefault()).name,
                submissions: engines.map(e => e.getSubmission('中文 a&b', null, 'keyword').uri.spec),
                cursor: !!w.document.getElementById('cursor-highlighter'),
                controls: [...w.document.querySelectorAll('.titlebar-button')].map(b => ({
                    content: w.getComputedStyle(b, '::before').content,
                    mask: w.getComputedStyle(b, '::before').maskImage,
                })),
                windows: w.matchMedia('(-moz-platform: windows)').matches,
                marker: Services.prefs.getStringPref('fingerprint-lite.test-marker'),
            };
        """)
        assert set(state["names"]) == {name for name, _, _ in browser_ui.SEARCH_ENGINES}
        assert state["default"] == "Startpage"
        assert state["marker"] == "existing profile"
        assert state["cursor"] is False
        for url in state["submissions"]:
            parsed = urlparse(url)
            assert parsed.scheme == "https"
            assert ["中文 a&b"] in parse_qs(parsed.query).values()
        if state["windows"]:
            assert state["controls"]
            assert all(c["content"] == '""' for c in state["controls"])
            assert all("data:image/svg+xml" in c["mask"] for c in state["controls"])
        browser.evaluate("""
            await search.setDefault(search.getEngineByName('DuckDuckGo'), search.CHANGE_REASON.USER);
            await search._settings._write();
            Services.prefs.savePrefFile(null);
            return (await search.getDefault()).name;
        """)
    with chrome_browser(executable, profile) as browser:
        assert (
            browser.evaluate("await search.init(); return (await search.getDefault()).name;")
            == "DuckDuckGo"
        )
