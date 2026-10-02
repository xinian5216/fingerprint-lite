"""Runtime repairs must preserve the upstream install and unrelated policies."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pytest

from camoufox_pm import browser_ui

V1_STUB = """
  async #getConfiguration(firstTime = true) {
    if (true) {
      return [{"appliesTo": [{"webExtension": {"id": "none@mozilla.org"}}]}];
    }
    let result = [];
    return result;
  }
"""


@pytest.fixture
def engine(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    (root / "version.json").write_text('{"version":"152.0.4","build":"beta.30"}')
    (root / "camoufox.exe").write_bytes(b"original executable")
    (root / "chrome.css").write_text("/* upstream theme */\n")
    (root / "distribution").mkdir()
    (root / "distribution/policies.json").write_text(
        json.dumps(
            {
                "policies": {
                    "DisableTelemetry": True,
                    "DisableAppUpdate": True,
                    "SearchEngines": {
                        "Remove": ["Google", "DuckDuckGo", "Bing", "Amazon.com"],
                        "PreventInstalls": True,
                        "Default": "None",
                        "Add": [{"Name": "None", "URLTemplate": "http://127.0.0.1"}],
                    },
                }
            }
        )
    )
    with ZipFile(root / "omni.ja", "w") as archive:
        archive.writestr(browser_ui._SELECTOR, V1_STUB)
        archive.writestr("unchanged.js", "original module")
    return root


def snapshot(root: Path):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_runtime_repairs_search_without_touching_upstream_or_other_modules(engine, tmp_path):
    original = snapshot(engine)
    executable = browser_ui.prepare_runtime(engine / "camoufox.exe", tmp_path / "cache")
    runtime = executable.parent
    assert snapshot(engine) == original
    assert executable.read_bytes() == b"original executable"
    with ZipFile(runtime / "omni.ja") as archive:
        selector = archive.read(browser_ui._SELECTOR).decode()
        assert '"recordType": "engine"' in selector
        assert "none@mozilla.org" not in selector
        assert archive.read("unchanged.js") == b"original module"
    policies = json.loads((runtime / "distribution/policies.json").read_text())["policies"]
    assert policies["DisableTelemetry"] is True
    assert policies["DisableAppUpdate"] is True
    assert policies["SearchSuggestEnabled"] is False
    search = policies["SearchEngines"]
    assert search["Default"] == "Startpage"
    assert search["Remove"] == ["Amazon.com", "None"]
    assert search["PreventInstalls"] is False
    assert [e["Name"] for e in search["Add"]] == [
        "Startpage",
        "DuckDuckGo",
        "Brave Search",
        "Google",
        "Bing",
    ]
    assert all("{searchTerms}" in e["URLTemplate"] for e in search["Add"])
    assert all("SuggestURLTemplate" not in e for e in search["Add"])


def test_relaunch_reuses_the_runtime_without_recopying(engine, tmp_path, monkeypatch):
    first = browser_ui.prepare_runtime(engine / "camoufox.exe", tmp_path / "cache")

    def unexpected_copy(*args, **kwargs):
        pytest.fail("A prepared runtime should be reused")

    monkeypatch.setattr(browser_ui.shutil, "copytree", unexpected_copy)
    assert browser_ui.prepare_runtime(engine / "camoufox.exe", tmp_path / "cache") == first


def test_concurrent_preparation_publishes_one_complete_runtime(engine, tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: browser_ui.prepare_runtime(engine / "camoufox.exe", tmp_path / "cache"),
                range(2),
            )
        )
    assert results[0] == results[1]
    assert not list((tmp_path / "cache").glob(".ui-*"))
    assert (results[0].parent / ".fingerprint-lite-ui-ready").is_file()


def test_failed_repair_does_not_publish_or_damage_the_source(engine, tmp_path):
    with ZipFile(engine / "omni.ja", "w") as archive:
        archive.writestr("unexpected.js", "unknown build")
    original = snapshot(engine)
    with pytest.raises(ValueError, match="search module was not found"):
        browser_ui.prepare_runtime(engine / "camoufox.exe", tmp_path / "cache")
    assert snapshot(engine) == original
    assert not list((tmp_path / "cache").iterdir())


def test_runtime_cache_inside_the_source_is_refused_before_copying(engine):
    original = snapshot(engine)
    with pytest.raises(ValueError, match="outside the source"):
        browser_ui.prepare_runtime(engine / "camoufox.exe", engine / "nested-cache")
    assert snapshot(engine) == original


def test_existing_cursor_config_is_disabled_without_mutating_the_pin(monkeypatch, tmp_path):
    config = {"showcursor": True, "fonts": ["Arial"], "fonts:spacing_seed": 123}
    monkeypatch.setattr(browser_ui, "prepare_runtime", lambda executable, cache: executable)
    result = browser_ui.prepare_launch_options(
        {
            "executable_path": str(tmp_path / "camoufox.exe"),
            "config": config,
        }
    )
    assert result["config"]["showcursor"] is False
    assert config["showcursor"] is True
    assert result["config"]["fonts"] == ["Arial"]
    assert result["config"]["fonts:spacing_seed"] == 123


def test_window_controls_cover_restore_and_use_the_current_button_colour():
    css = browser_ui._window_controls_css()
    for state in ("min", "max", "restore", "close"):
        assert f".titlebar-{state}::before" in css
    assert "currentColor" in css
    assert "data:image/svg+xml" in css
    assert "font-family" not in css
