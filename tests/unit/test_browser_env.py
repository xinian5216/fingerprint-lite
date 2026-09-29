"""The pinned Camoufox browser: one verified install path for every source.

A portable install must run one fixed browser build — not whatever is newest,
not whatever happens to be cached. Online or from a local official ZIP, the
same version pin, the same SHA256 verification and the same atomic install
apply; a failed download or extraction must leave an existing browser and the
profile data untouched.

Tests point camoufox's managed install at a throwaway folder and build tiny
synthetic ZIPs, so the real install machinery runs without any network and
without touching the developer's browser cache.
"""

import asyncio
import hashlib
import json
import posixpath
import sys
import zipfile
from pathlib import Path

import pytest

from camoufox_pm import browser_env, portable
from camoufox_pm.core import browser_session as bs


@pytest.fixture
def camoufox_home(tmp_path, monkeypatch):
    """Point camoufox's managed install (and geoip cache) at the test folder."""
    from camoufox import geolocation, multiversion, pkgman

    install_dir = tmp_path / "camoufox-cache"
    monkeypatch.setattr(pkgman, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(multiversion, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(multiversion, "BROWSERS_DIR", install_dir / "browsers")
    monkeypatch.setattr(multiversion, "CONFIG_FILE", install_dir / "config.json")
    monkeypatch.setattr(multiversion, "REPO_CACHE_FILE", install_dir / "repo_cache.json")
    monkeypatch.setattr(multiversion, "COMPAT_FLAG", install_dir / ".0.5_FLAG")
    # Firefox's own app directory belongs to the user's home; not this test's business.
    monkeypatch.setattr(pkgman, "ensure_browser_profile_dir", lambda *a, **k: None)
    monkeypatch.setattr(geolocation, "GEOIP_DIR", tmp_path / "geoip")
    monkeypatch.setattr(geolocation, "MMDB_DIR", tmp_path / "geoip" / "mmdb")
    monkeypatch.setattr(geolocation, "GEOIP_CONFIG", tmp_path / "geoip" / "config.yml")
    browser_env.reset_install_state()
    yield install_dir
    browser_env.reset_install_state()


def test_selected_browser_root_controls_camoufox_active_path(tmp_path, monkeypatch):
    from camoufox import multiversion, pkgman

    stale_root = tmp_path / "old-global-cache"
    browser_dir = tmp_path / "selected-Browser"
    monkeypatch.setattr(pkgman, "INSTALL_DIR", stale_root)
    monkeypatch.setattr(multiversion, "INSTALL_DIR", stale_root)
    monkeypatch.setattr(browser_env, "_BROWSER_DIR", None)

    browser_env.use_browser_root(browser_dir)
    relative = "browsers/official/152.0.4-beta.30-testpin"
    expected = browser_dir / "cache" / relative
    expected.mkdir(parents=True)
    (expected / "version.json").write_text("{}", encoding="utf-8")
    multiversion.set_active(relative)

    assert pkgman.INSTALL_DIR == browser_dir / "cache"
    assert multiversion.INSTALL_DIR == browser_dir / "cache"
    assert multiversion.get_active_path() == expected


@pytest.fixture
def no_download(monkeypatch):
    """Fail the test loudly if anything tries to reach the network."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("must not download")

    monkeypatch.setattr(browser_env, "install_from_download", refuse)


def synthetic_browser_entry() -> str:
    """Executable path inside a synthetic browser ZIP or install folder.

    Derived from camoufox's own ``LAUNCH_FILE`` table — the same table the
    product's ``installed_path()`` → ``launch_path()`` completeness check
    enforces — so the fixture tracks the integrity rule instead of restating
    it. A hardcoded ``camoufox.exe`` is correctly rejected as an incomplete
    install on Linux/macOS, which is what broke this suite off Windows.
    """
    from camoufox import pkgman

    name = pkgman.LAUNCH_FILE[pkgman.OS_NAME]
    if pkgman.OS_NAME == "mac":
        # launch_path() reads this relative to Camoufox.app/Contents/Resources.
        name = posixpath.normpath(posixpath.join("Camoufox.app/Contents/Resources", name))
    return name


def launch_exe(install_dir: Path, folder: Path) -> Path:
    return folder / synthetic_browser_entry()


def fake_zip(path: Path) -> tuple[Path, str]:
    """Write a synthetic browser ZIP; return it and its real SHA256."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(synthetic_browser_entry(), b"fake browser payload")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def make_complete_install(install_dir: Path, sha256: str | None = None) -> Path:
    """Build the exact folder layout a verified install leaves behind."""
    sha = sha256 or browser_env.PINNED_SHA256
    folder = (
        install_dir
        / "browsers"
        / "official"
        / f"{browser_env.PINNED_VERSION}-{browser_env.PINNED_BUILD}-{sha[:8]}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    exe = launch_exe(install_dir, folder)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"fake browser payload")
    (folder / "version.json").write_text(
        json.dumps(
            {
                "version": browser_env.PINNED_VERSION,
                "build": browser_env.PINNED_BUILD,
                "sha256": sha,
            }
        )
    )
    return folder


# ---------------------------------------------------------------------------
# Reuse and completeness
# ---------------------------------------------------------------------------


def test_a_complete_install_is_reused_without_touching_the_network(camoufox_home, no_download):
    folder = make_complete_install(camoufox_home)
    assert browser_env.ensure_browser(allow_download=False) == folder


def test_an_incomplete_install_is_not_reused(camoufox_home, monkeypatch):
    folder = make_complete_install(camoufox_home)
    launch_exe(camoufox_home, folder).unlink()  # the payload is what makes it complete

    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return make_complete_install(camoufox_home)

    monkeypatch.setattr(browser_env, "install_from_download", fake_download)
    assert browser_env.ensure_browser() is not None
    assert calls, "a broken install must be replaced, not trusted"


# ---------------------------------------------------------------------------
# The offline ZIP path — same pin, same verification, same atomic install
# ---------------------------------------------------------------------------


def test_an_offline_zip_installs_through_the_same_verified_path(
    camoufox_home, no_download, tmp_path, monkeypatch
):
    from camoufox import multiversion

    offline = tmp_path / "Browser"
    offline.mkdir()
    zip_path, digest = fake_zip(offline / "official-browser.zip")
    monkeypatch.setattr(browser_env, "PINNED_SHA256", digest)
    installed = browser_env.ensure_browser(offline_dir=offline, allow_download=False)

    assert installed.name.endswith(digest[:8])
    metadata = json.loads((installed / "version.json").read_text())
    assert metadata["sha256"] == digest
    assert "official/" + installed.name in multiversion.load_config()["active_version"]
    assert zip_path.exists()


def test_the_download_path_installs_through_the_same_machinery(
    camoufox_home, tmp_path, monkeypatch
):
    """The online source must reach the shared installer — without looping on itself."""
    from camoufox.pkgman import CamoufoxFetcher

    served, digest = fake_zip(tmp_path / "served.zip")
    monkeypatch.setattr(browser_env, "PINNED_SHA256", digest)
    calls = []

    def serve(file, url):
        """Shaped like the real one: download_file is a staticmethod, no self."""
        calls.append(url)
        file.write(served.read_bytes())
        return file

    monkeypatch.setattr(CamoufoxFetcher, "download_file", serve)
    installed = browser_env.install_from_download()

    assert calls, "the download must be attempted"
    assert installed.name.endswith(digest[:8])
    assert browser_env.installed_path() == installed


def test_a_zip_with_the_wrong_digest_is_refused_and_nothing_changes(
    camoufox_home, no_download, tmp_path
):
    offline = tmp_path / "Browser"
    offline.mkdir()
    zip_path, _digest = fake_zip(offline / "official-browser.zip")

    with pytest.raises(browser_env.BrowserInstallError) as excinfo:
        browser_env.ensure_browser(offline_dir=offline, allow_download=False)

    message = str(excinfo.value)
    assert "official-browser.zip" in message
    assert browser_env.PINNED_SHA256[:8] in message
    assert not (camoufox_home / "browsers").exists(), "no half-install may be left behind"
    assert zip_path.exists(), "the user's file is not ours to delete"


def test_a_failed_reinstall_leaves_the_existing_browser_alone(camoufox_home, no_download, tmp_path):
    folder = make_complete_install(camoufox_home)
    offline = tmp_path / "Browser"
    offline.mkdir()
    fake_zip(offline / "wrong.zip")

    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.ensure_browser(offline_dir=offline, allow_download=False, replace=True)

    assert launch_exe(camoufox_home, folder).read_bytes() == b"fake browser payload"
    assert browser_env.installed_path() == folder


def test_a_failed_extraction_leaves_no_partial_install(
    camoufox_home, no_download, tmp_path, monkeypatch
):
    from camoufox import multiversion

    offline = tmp_path / "Browser"
    offline.mkdir()
    zip_path, digest = fake_zip(offline / "browser.zip")

    def explode(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(multiversion, "unzip", explode)
    monkeypatch.setattr(browser_env, "PINNED_SHA256", digest)

    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.ensure_browser(offline_dir=offline, allow_download=False)

    assert browser_env.installed_path() is None
    browsers = camoufox_home / "browsers"
    leftovers = [p for p in browsers.rglob("*") if p.is_file()] if browsers.exists() else []
    assert leftovers == [], "no partial install survives a failure"
    assert zip_path.exists()


# ---------------------------------------------------------------------------
# The download path — once per session, manual entry after a failure
# ---------------------------------------------------------------------------


def test_download_failure_names_the_manual_entry_and_is_not_retried(camoufox_home, monkeypatch):
    attempts = []

    def failing_download(**kwargs):
        attempts.append(kwargs)
        raise OSError("the network is gone")

    monkeypatch.setattr(browser_env, "install_from_download", failing_download)
    with pytest.raises(browser_env.BrowserInstallError) as excinfo:
        browser_env.ensure_browser()
    message = str(excinfo.value)
    assert "Browser" in message and "browser install" in message
    assert len(attempts) == 1

    def must_not_run(**kwargs):
        raise AssertionError("a failed download must not be retried on its own")

    monkeypatch.setattr(browser_env, "install_from_download", must_not_run)
    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.ensure_browser()
    assert len(attempts) == 1, "one automatic attempt per session"


def test_a_manual_retry_stays_available_after_a_failure(camoufox_home, monkeypatch):
    """Not being pushy is not the same as being stuck: `browser install` retries."""

    def failing_download(**kwargs):
        raise OSError("gone")

    monkeypatch.setattr(browser_env, "install_from_download", failing_download)
    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.ensure_browser()

    attempts = []

    def manual_download(**kwargs):
        attempts.append(kwargs)
        return Path("C:/somewhere")

    monkeypatch.setattr(browser_env, "install_from_download", manual_download)
    assert browser_env.install_from_download() is not None  # explicit, user-asked retry
    assert attempts


def test_an_explicit_zip_is_digest_checked_even_when_a_browser_is_installed(
    camoufox_home, no_download, tmp_path
):
    """The user handed us a file: say whether it is the pinned one, do not shrug."""
    make_complete_install(camoufox_home)
    zip_path, _digest = fake_zip(tmp_path / "not-the-pinned-one.zip")

    with pytest.raises(browser_env.BrowserInstallError) as excinfo:
        browser_env.install_from_zip(zip_path)

    assert "not-the-pinned-one.zip" in str(excinfo.value)
    assert "Nothing was changed" in str(excinfo.value)
    assert browser_env.installed_path() is not None, "the working browser stays"


def test_the_manual_door_installs_the_official_zip_while_the_app_runs(camoufox_home, tmp_path):
    """`browser install <zip>` is the recovery entry — and it is the same install."""
    zip_path, digest = fake_zip(tmp_path / "recovery.zip")
    from unittest import mock

    with mock.patch.object(browser_env, "PINNED_SHA256", digest):
        installed = browser_env.install_from_zip(zip_path)
    assert installed.name.endswith(digest[:8])


def test_startup_still_prepares_geoip_when_the_browser_is_already_installed(
    camoufox_home, no_download, monkeypatch
):
    """A pre-installed browser must not skip the rest of the environment."""
    make_complete_install(camoufox_home)
    prepared = []
    monkeypatch.setattr(browser_env, "ensure_geoip", lambda: prepared.append(True))

    thread = browser_env.ensure_in_background()
    assert thread is not None, "the environment job must still run"
    thread.join(timeout=5)
    assert prepared, "geoip has to be prepared even when the browser is reused"


# ---------------------------------------------------------------------------
# Never an unverified browser
# ---------------------------------------------------------------------------


def test_the_official_asset_digest_must_match_the_pin(camoufox_home, monkeypatch, tmp_path):
    """The pin is not decoration: a differently-signed zip is refused outright."""
    offline = tmp_path / "Browser"
    offline.mkdir()
    fake_zip(offline / "looks-official.zip")

    monkeypatch.setattr(browser_env, "PINNED_SHA256", "0" * 64)
    monkeypatch.setattr(browser_env, "PINNED_VERSION", "999.0.0")
    monkeypatch.setattr(browser_env, "PINNED_BUILD", "beta.1")

    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.ensure_browser(offline_dir=offline, allow_download=False)
    assert browser_env.installed_path() is None


def test_status_describes_the_pinned_browser(camoufox_home, no_download):
    status = browser_env.browser_status()
    assert status.installed is False

    folder = make_complete_install(camoufox_home)
    status = browser_env.browser_status()
    assert status.installed is True
    assert status.path == folder
    assert status.sha256 == browser_env.PINNED_SHA256


# ---------------------------------------------------------------------------
# The manual door on the command line
# ---------------------------------------------------------------------------


def test_the_cli_installs_from_a_zip_and_reports_the_path(
    camoufox_home, no_download, tmp_path, monkeypatch, capsys
):
    from camoufox_pm import cli

    monkeypatch.chdir(tmp_path)  # installs must land in the test's tree, not the repo
    zip_path, digest = fake_zip(tmp_path / "official-browser.zip")
    monkeypatch.setattr(browser_env, "PINNED_SHA256", digest)
    monkeypatch.setattr(sys, "argv", ["camoufox-pm", "browser", "install", str(zip_path)])

    cli.main()

    out = capsys.readouterr().out
    assert "ready at" in out
    assert digest[:8] in out, "the report names the install that was verified"


def test_the_cli_reports_a_rejected_zip_with_a_clear_error(
    camoufox_home, no_download, tmp_path, monkeypatch, capsys
):
    from camoufox_pm import cli

    zip_path, _digest = fake_zip(tmp_path / "not-official.zip")
    monkeypatch.setattr(sys, "argv", ["camoufox-pm", "browser", "install", str(zip_path)])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "not-official.zip" in err
    assert "Nothing was changed" in err


def test_the_cli_status_names_the_pinned_browser(camoufox_home, no_download, monkeypatch, capsys):
    from camoufox_pm import cli

    monkeypatch.setattr(sys, "argv", ["camoufox-pm", "browser", "status"])
    cli.main()

    out = capsys.readouterr().out
    assert browser_env.pin_version_string() in out
    assert "installed      : no" in out


# ---------------------------------------------------------------------------
# The launch guard: no silent fallback to an unverified browser
# ---------------------------------------------------------------------------


def test_launch_refuses_while_the_pinned_browser_is_missing(camoufox_home, monkeypatch):
    def refuse():
        raise browser_env.BrowserInstallError("pinned browser is not ready")

    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
    monkeypatch.setattr(browser_env, "ensure_ready", refuse)
    monkeypatch.setattr(portable, "is_active", lambda: True)

    manager = bs.BrowserSessionManager()
    with pytest.raises(browser_env.BrowserInstallError):
        asyncio.run(manager.launch("p1", {}))


# ---------------------------------------------------------------------------
# Install progress: observed stages and byte counts, same verified install
# ---------------------------------------------------------------------------


def test_a_zip_install_reports_bytes_stages_and_done(camoufox_home, tmp_path, monkeypatch):
    """Progress is observed, not reimplemented: bytes move, stages advance."""
    offline = tmp_path / "Browser"
    offline.mkdir()
    zip_path, digest = fake_zip(offline / "official-browser.zip")
    monkeypatch.setattr(browser_env, "PINNED_SHA256", digest)
    seen = []
    browser_env.set_browser_progress_listener(seen.append)

    installed = browser_env.install_from_zip(zip_path)

    assert installed.is_dir()
    stages = [event["stage"] for event in seen]
    assert stages[0] == "downloading"
    assert "verifying" in stages
    assert "extracting" in stages
    assert stages[-1] == "done"
    downloads = [event for event in seen if event["stage"] == "downloading"]
    assert downloads[-1]["downloaded"] == downloads[-1]["total"] == zip_path.stat().st_size
    snapshot = browser_env.browser_progress()
    assert snapshot["stage"] == "done" and snapshot["error"] is None
    browser_env.set_browser_progress_listener(None)


def test_a_failed_install_reports_failed_and_keeps_the_error(camoufox_home, tmp_path, monkeypatch):
    from camoufox import multiversion

    offline = tmp_path / "Browser"
    offline.mkdir()
    zip_path, digest = fake_zip(offline / "official-browser.zip")
    monkeypatch.setattr(browser_env, "PINNED_SHA256", digest)
    monkeypatch.setattr(
        multiversion, "unzip", lambda *a, **k: (_ for _ in ()).throw(OSError("disk went away"))
    )

    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.install_from_zip(zip_path)

    snapshot = browser_env.browser_progress()
    assert snapshot["stage"] == "failed"
    assert snapshot["error"]
