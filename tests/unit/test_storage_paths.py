"""Storage paths: everything on the chosen disk, nothing silently on C:.

The portable edition treats the folder it runs from as its root and keeps
user data, the browser cache and temporary files under it by default. A first
start may relocate Data and Browser (via ``paths.env`` beside the program or
environment variables) — decided before the backend starts. Changing where
Data lives later is an explicit, confirmed migration that never deletes the
old data and rolls back by construction.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from camoufox_pm import browser_env, portable


@pytest.fixture
def protected_env(monkeypatch):
    """Track every environment key the code under test writes."""
    for key in ("CPM_DATA_DIR", "CPM_BROWSER_DIR", "CPM_TEMP_DIR", "CPM_DB_PATH", "CPM_SECRET_KEY"):
        monkeypatch.setenv(key, "sentinel")
        os.environ.pop(key, None)
    monkeypatch.setattr(tempfile, "tempdir", None)
    return monkeypatch


# ---------------------------------------------------------------------------
# Defaults and customization
# ---------------------------------------------------------------------------


def test_temp_lives_under_the_program_root_by_default(tmp_path, protected_env):
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    assert ctx.temp_dir == tmp_path / "Temp"
    assert ctx.browser_dir == tmp_path / "Browser"
    assert ctx.temp_dir.is_dir()


def test_paths_env_beside_the_program_relocates_data_browser_and_temp(tmp_path, protected_env):
    (tmp_path / "paths.env").write_text(
        "CPM_DATA_DIR=elsewhere/Data\nCPM_BROWSER_DIR=elsewhere/Browser\nCPM_TEMP_DIR=elsewhere/Temp\n",
        encoding="utf-8",
    )
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    assert ctx.data_dir == tmp_path / "elsewhere" / "Data"
    assert ctx.browser_dir == tmp_path / "elsewhere" / "Browser"
    assert ctx.temp_dir == tmp_path / "elsewhere" / "Temp"


def test_a_paths_file_with_a_byte_order_mark_still_works(tmp_path, protected_env):
    """Windows editors save UTF-8 with a BOM; the first key must survive it."""
    (tmp_path / "paths.env").write_text(
        "\ufeffCPM_DATA_DIR=elsewhere/Data\nCPM_BROWSER_DIR=elsewhere/Browser\n",
        encoding="utf-8",
    )
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    assert ctx.data_dir == tmp_path / "elsewhere" / "Data"
    assert ctx.browser_dir == tmp_path / "elsewhere" / "Browser"


def test_the_real_environment_wins_over_paths_env(tmp_path, protected_env, monkeypatch):
    (tmp_path / "paths.env").write_text("CPM_DATA_DIR=ignored-by-env\n", encoding="utf-8")
    monkeypatch.setenv("CPM_DATA_DIR", str(tmp_path / "env-data"))
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    assert ctx.data_dir == tmp_path / "env-data"


def test_a_relative_path_is_read_from_the_program_root(tmp_path, protected_env, monkeypatch):
    monkeypatch.setenv("CPM_DATA_DIR", "MyData")
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    assert ctx.data_dir == tmp_path / "MyData"


# ---------------------------------------------------------------------------
# Temporary files follow the Temp folder (process-locally, never system-global)
# ---------------------------------------------------------------------------


def test_temporary_files_go_to_the_temp_folder(tmp_path, protected_env):
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    assert tempfile.gettempdir() == str(ctx.temp_dir)
    assert os.environ["TEMP"] == str(ctx.temp_dir)
    assert os.environ["TMP"] == str(ctx.temp_dir)

    handle = tempfile.NamedTemporaryFile(delete=False)
    handle.close()
    try:
        assert Path(handle.name).parent == ctx.temp_dir
    finally:
        os.unlink(handle.name)


# ---------------------------------------------------------------------------
# Validity: paths, permissions, disk space
# ---------------------------------------------------------------------------


def test_a_full_disk_is_refused_before_anything_is_written(tmp_path, protected_env, monkeypatch):
    monkeypatch.setattr(portable, "_free_bytes", lambda _path: 1024)
    with pytest.raises(portable.NotEnoughSpace) as excinfo:
        portable.bootstrap(
            ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
        )
    assert "space" in str(excinfo.value).lower()
    assert not (tmp_path / "Data" / "profiles.db").exists()


# ---------------------------------------------------------------------------
# The browser cache and GeoIP follow the Browser folder
# ---------------------------------------------------------------------------


@pytest.fixture
def camoufox_constants_restored():
    """use_browser_root moves camoufox's path constants; put them back after."""
    from camoufox import geolocation, multiversion, pkgman

    saved = {
        (pkgman, "INSTALL_DIR"): pkgman.INSTALL_DIR,
        (multiversion, "INSTALL_DIR"): multiversion.INSTALL_DIR,
        (multiversion, "BROWSERS_DIR"): multiversion.BROWSERS_DIR,
        (multiversion, "CONFIG_FILE"): multiversion.CONFIG_FILE,
        (multiversion, "REPO_CACHE_FILE"): multiversion.REPO_CACHE_FILE,
        (multiversion, "COMPAT_FLAG"): multiversion.COMPAT_FLAG,
        (geolocation, "GEOIP_DIR"): geolocation.GEOIP_DIR,
        (geolocation, "MMDB_DIR"): geolocation.MMDB_DIR,
        (geolocation, "GEOIP_CONFIG"): geolocation.GEOIP_CONFIG,
    }
    yield
    for (module, name), value in saved.items():
        setattr(module, name, value)
    browser_env._BROWSER_DIR = None


def test_the_browser_cache_and_geoip_follow_the_browser_folder(
    tmp_path, protected_env, camoufox_constants_restored
):
    from camoufox import geolocation, multiversion, pkgman

    browser_dir = tmp_path / "Browser"
    browser_env.use_browser_root(browser_dir)

    cache = browser_dir / "cache"
    assert pkgman.INSTALL_DIR == cache
    assert multiversion.INSTALL_DIR == cache
    assert multiversion.BROWSERS_DIR == cache / "browsers"
    assert geolocation.MMDB_DIR == cache / "geoip" / "mmdb"
    assert browser_env.default_offline_dir() == browser_dir, "the drop-in folder stays user-facing"


def test_browser_installs_work_on_the_browser_volume(
    tmp_path, protected_env, monkeypatch, camoufox_constants_restored
):
    """Download and extraction share the destination disk, whatever TEMP is."""
    from camoufox import multiversion

    browser_dir = tmp_path / "Browser"
    browser_env.use_browser_root(browser_dir)
    offline = tmp_path / "zips"
    offline.mkdir()
    import hashlib
    import zipfile

    zip_path = offline / "browser.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("camoufox.exe", b"payload")
    monkeypatch.setattr(
        browser_env, "PINNED_SHA256", hashlib.sha256(zip_path.read_bytes()).hexdigest()
    )

    seen = {}

    def record_temp(*_args, **_kwargs):
        seen["tempdir"] = tempfile.gettempdir()
        raise OSError("stop here")

    monkeypatch.setattr(multiversion, "unzip", record_temp)
    with pytest.raises(browser_env.BrowserInstallError):
        browser_env.install_from_zip(zip_path)

    assert seen["tempdir"] == str(browser_dir / ".tmp")


def test_a_browser_download_needs_the_space_for_it(
    tmp_path, protected_env, monkeypatch, camoufox_constants_restored
):
    browser_dir = tmp_path / "Browser"
    browser_env.use_browser_root(browser_dir)
    monkeypatch.setattr(portable, "_free_bytes", lambda _path: 10)
    with pytest.raises(browser_env.BrowserInstallError) as excinfo:
        browser_env.install_from_download()
    assert "space" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Moving Data: explicit, confirmed, with the old data kept
# ---------------------------------------------------------------------------


def run_cli(*args: str) -> None:
    import sys

    from camoufox_pm import cli

    monkeyed = ["camoufox-pm", *args]
    old = sys.argv
    sys.argv = monkeyed
    try:
        cli.main()
    finally:
        sys.argv = old


def make_data(root: Path) -> Path:
    data = root / "Data"
    (data / "profiles").mkdir(parents=True)
    (data / "logs").mkdir()
    (data / "config.env").write_text("CPM_SECRET_KEY=stable-key\n", encoding="utf-8")
    conn = sqlite3.connect(data / "profiles.db")
    conn.execute("create table t (x)")
    conn.execute("insert into t values (1)")
    conn.commit()
    conn.close()
    (data / "profiles" / "profile_x" / "cookies.sqlite").parent.mkdir(parents=True)
    (data / "profiles" / "profile_x" / "cookies.sqlite").write_bytes(b"sqlite-ish")
    return data


def test_data_migrate_moves_the_whole_tree_and_keeps_the_old_one(
    tmp_path, protected_env, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CPM_DATA_DIR", str(tmp_path / "Data"))
    make_data(tmp_path)
    target = tmp_path / "D-drive" / "Data"

    run_cli("data", "migrate", str(target), "--yes")

    assert (target / "profiles.db").exists()
    assert (target / "profiles" / "profile_x" / "cookies.sqlite").read_bytes() == b"sqlite-ish"
    assert (target / "config.env").read_text(encoding="utf-8") == "CPM_SECRET_KEY=stable-key\n"
    conn = sqlite3.connect(target / "profiles.db")
    assert conn.execute("select x from t").fetchone() == (1,)
    conn.close()

    backups = list(tmp_path.glob("Data.bak-*"))
    assert backups, "the old data must be kept as the rollback"
    assert (backups[0] / "profiles.db").exists()

    paths = (tmp_path / "paths.env").read_text(encoding="utf-8")
    assert str(target) in paths, "the new location is recorded beside the program"


def test_a_failed_migration_leaves_the_old_data_alone(tmp_path, protected_env, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CPM_DATA_DIR", str(tmp_path / "Data"))
    make_data(tmp_path)
    target = tmp_path / "elsewhere" / "Data"

    def explode(*_args, **_kwargs):
        raise OSError("the copy died")

    monkeypatch.setattr(portable, "_copy_tree_verified", explode)
    with pytest.raises(SystemExit):
        run_cli("data", "migrate", str(target), "--yes")

    assert (tmp_path / "Data" / "profiles.db").exists(), "source untouched"
    assert not target.exists(), "no half-copied tree left behind"
    assert not (tmp_path / "paths.env").exists(), "the location is not switched on failure"


def test_migration_needs_a_yes_or_an_answer(tmp_path, protected_env, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CPM_DATA_DIR", str(tmp_path / "Data"))
    make_data(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_: "no")

    run_cli("data", "migrate", str(tmp_path / "target"))

    assert (tmp_path / "Data" / "profiles.db").exists()
    assert not (tmp_path / "target").exists()
