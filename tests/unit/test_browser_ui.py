"""Browser defaults must preserve user data and pinned font fingerprints."""

import json

import pytest

from camoufox_pm import browser_ui
from camoufox_pm.core.models import BrowserSettings, Profile


@pytest.fixture
def install(tmp_path):
    folder = tmp_path / "下载" / "Browser"
    distribution = folder / "distribution"
    distribution.mkdir(parents=True)
    original = {
        "policies": {
            "DisableTelemetry": True,
            "DisableAppUpdate": True,
            "Extensions": {"Uninstall": ["google@search.mozilla.org", "webcompat@mozilla.org"]},
            "SearchEngines": {"Default": "None", "Remove": ["DuckDuckGo"]},
        }
    }
    (distribution / "policies.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(original).encode())
    return folder


def test_policy_replaces_none_preserves_other_policies_and_exact_backup(install):
    path = install / "distribution" / "policies.json"
    original = path.read_bytes()
    browser_ui.prepare_search_policy(install)
    policies = json.loads(path.read_text(encoding="utf-8"))["policies"]
    assert policies["DisableTelemetry"] is True
    assert policies["DisableAppUpdate"] is True
    assert policies["Extensions"]["Uninstall"] == [
        "google@search.mozilla.org",
        "webcompat@mozilla.org",
    ]
    search = policies["SearchEngines"]
    assert search["Default"] == search["DefaultPrivate"] == "DuckDuckGo"
    assert search["Remove"] == ["None"]
    assert search["PreventInstalls"] is False
    assert {engine["Name"] for engine in search["Add"]} == {
        "DuckDuckGo",
        "Startpage",
        "Brave Search",
        "Google",
        "Bing",
    }
    assert all(engine["URLTemplate"].startswith("https://") for engine in search["Add"])
    assert path.with_name("policies.fingerprint-lite-original.json").read_bytes() == original


def test_repeated_preparation_does_not_rewrite_policy_or_backup(install, monkeypatch):
    browser_ui.prepare_search_policy(install)

    def refuse(*args, **kwargs):
        raise AssertionError("an unchanged policy must not be replaced")

    monkeypatch.setattr(browser_ui.os, "replace", refuse)
    browser_ui.prepare_search_policy(install)


def test_failed_replace_leaves_original_and_removes_temporary_file(install, monkeypatch):
    path = install / "distribution" / "policies.json"
    original = path.read_bytes()

    def refuse(*args):
        raise PermissionError("read only")

    monkeypatch.setattr(browser_ui.os, "replace", refuse)
    with pytest.raises(PermissionError):
        browser_ui.prepare_search_policy(install)
    assert path.read_bytes() == original
    assert {p.name for p in path.parent.iterdir()} == {
        "policies.json",
        "policies.fingerprint-lite-original.json",
    }


def test_invalid_policy_is_not_replaced(install):
    path = install / "distribution" / "policies.json"
    path.write_text("broken JSON", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        browser_ui.prepare_search_policy(install)
    assert path.read_text(encoding="utf-8") == "broken JSON"
    assert not path.with_name("policies.fingerprint-lite-original.json").exists()


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_native_titlebar_uses_host_platform_without_changing_profile_fonts(monkeypatch, platform):
    monkeypatch.setattr(browser_ui.sys, "platform", platform)
    profile = Profile(
        name="existing", browser_settings=BrowserSettings(os="macos", fonts=["Arial"])
    )
    options = profile.to_camoufox_launch_options()
    assert options["fonts"] == ["Arial"]
    assert options["config"]["showcursor"] is False
    assert profile.browser_settings.fonts == ["Arial"]
    prefs = options["firefox_user_prefs"]
    assert prefs["browser.search.suggest.enabled"] is False
    if platform == "win32":
        assert prefs["browser.tabs.inTitlebar"] == 0
    else:
        assert "browser.tabs.inTitlebar" not in prefs
