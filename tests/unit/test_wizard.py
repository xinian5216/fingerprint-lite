"""First-start wizard: choose the folders once, keep what already exists.

The wizard must be a thin layer over the machinery that already exists —
paths.env, the path validation, and the data migration. What the tests pin
down beyond that: defaults follow the program's disk, and existing data keeps
its original secret key with no silent overwrite anywhere.
"""

import json
import os
import sqlite3
import types
from pathlib import Path

import pytest

from camoufox_pm import browser_env, portable, wizard


@pytest.fixture
def protected_env(monkeypatch):
    for key in (
        "CPM_DATA_DIR",
        "CPM_BROWSER_DIR",
        "CPM_TEMP_DIR",
        "CPM_DB_PATH",
        "CPM_SECRET_KEY",
        wizard.ANSWERS_ENV,
    ):
        monkeypatch.setenv(key, "sentinel")
        os.environ.pop(key, None)
    monkeypatch.setattr(portable, "_ACTIVE", False)
    return monkeypatch


@pytest.fixture
def camoufox_constants_restored():
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


def seed_data(data_dir: Path, key: str = "original-key") -> Path:
    (data_dir / "profiles").mkdir(parents=True)
    (data_dir / "logs").mkdir()
    (data_dir / "config.env").write_text(f"CPM_SECRET_KEY={key}\n", encoding="utf-8")
    conn = sqlite3.connect(data_dir / "profiles.db")
    conn.execute("create table profiles (id)")
    conn.execute("insert into profiles values ('p1')")
    conn.commit()
    conn.close()
    return data_dir


# ---------------------------------------------------------------------------
# Defaults and when the wizard appears
# ---------------------------------------------------------------------------


def test_defaults_follow_the_program_disk(tmp_path):
    answers = wizard.default_answers(tmp_path)
    assert answers.data_dir == tmp_path / "Data"
    assert answers.browser_dir == tmp_path / "Browser"
    assert answers.temp_dir == tmp_path / "Temp"


def test_first_desktop_start_offers_the_wizard(tmp_path, protected_env):
    assert wizard.should_show_wizard(tmp_path, ["FingerprintLite", "--desktop"], os.environ)


def test_a_configured_install_does_not_ask_again(tmp_path, protected_env):
    (tmp_path / "paths.env").write_text("CPM_DATA_DIR=Data\n", encoding="utf-8")
    assert not wizard.should_show_wizard(tmp_path, ["FingerprintLite", "--desktop"], os.environ)


def test_server_and_cli_runs_never_pop_the_wizard(tmp_path, protected_env):
    assert not wizard.should_show_wizard(tmp_path, ["camoufox-pm"], os.environ)
    assert not wizard.should_show_wizard(tmp_path, ["camoufox-pm", "user", "add"], os.environ)


def test_the_wizard_flag_always_wins(tmp_path, protected_env):
    assert wizard.should_show_wizard(tmp_path, ["camoufox-pm", "--wizard"], os.environ)


# ---------------------------------------------------------------------------
# Applying the choice
# ---------------------------------------------------------------------------


def test_applying_records_the_choice_over_old_path_values(tmp_path, protected_env):
    """The wizard's three choices are explicit — they replace path values, once,
    with the user watching. What must never be replaced silently is data and
    its key; that is pinned down by the migration tests below."""
    (tmp_path / "paths.env").write_text("CPM_TEMP_DIR=oldvalue\n", encoding="utf-8")
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "chosen" / "Data",
        browser_dir=tmp_path / "chosen" / "Browser",
        temp_dir=tmp_path / "chosen" / "Temp",
        browser_source="skip",
    )
    result = wizard.apply_answers(tmp_path, answers, environ=os.environ)

    assert result.data_dir == tmp_path / "chosen" / "Data"
    written = portable.load_path_overrides(tmp_path)
    assert written["CPM_DATA_DIR"] == str(tmp_path / "chosen" / "Data")
    assert written["CPM_TEMP_DIR"] == str(tmp_path / "chosen" / "Temp")
    assert (result.data_dir / "profiles").is_dir()


def test_existing_data_moves_over_and_keeps_its_original_key(tmp_path, protected_env):
    old = seed_data(tmp_path / "Data")
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "E" / "Data",
        browser_dir=tmp_path / "E" / "Browser",
        temp_dir=tmp_path / "E" / "Temp",
        browser_source="skip",
        data_choice="migrate",
    )
    result = wizard.apply_answers(tmp_path, answers, environ=os.environ)

    moved_key = (result.data_dir / "config.env").read_text(encoding="utf-8")
    assert "CPM_SECRET_KEY=original-key" in moved_key, "the key must not be regenerated"
    conn = sqlite3.connect(result.data_dir / "profiles.db")
    assert conn.execute("select id from profiles").fetchone() == ("p1",)
    conn.close()
    assert result.migrated_from is not None and result.migrated_from.name.startswith("Data.bak-")
    assert (result.migrated_from / "config.env").exists(), "the old copy is the rollback"
    assert old.name == "Data"


def test_existing_data_needs_an_explicit_choice(tmp_path, protected_env):
    """Nothing may silently move away from data that already exists."""
    seed_data(tmp_path / "Data", key="original-key")
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "E" / "Data",
        browser_dir=tmp_path / "E" / "Browser",
        temp_dir=tmp_path / "E" / "Temp",
        browser_source="skip",
    )
    with pytest.raises(portable.PortableError) as excinfo:
        wizard.apply_answers(tmp_path, answers, environ=os.environ)
    assert "migrate" in str(excinfo.value).lower()
    assert (tmp_path / "Data" / "profiles.db").exists(), "the old data stays put"
    assert not (tmp_path / "E" / "Data").exists(), "and nothing is created over there yet"


def test_a_fresh_start_choice_leaves_the_old_data_and_its_key_untouched(tmp_path, protected_env):
    """'Start fresh' abandons the reference on purpose — never the data itself."""
    seed_data(tmp_path / "Data", key="original-key")
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "E" / "Data",
        browser_dir=tmp_path / "E" / "Browser",
        temp_dir=tmp_path / "E" / "Temp",
        browser_source="skip",
        data_choice="fresh",
    )
    result = wizard.apply_answers(tmp_path, answers, environ=os.environ)

    old_key = (tmp_path / "Data" / "config.env").read_text(encoding="utf-8")
    assert old_key == "CPM_SECRET_KEY=original-key\n", "the old key file is untouched"
    assert (tmp_path / "Data" / "profiles.db").exists(), "the old data is still there"
    assert result.migrated_from is None, "nothing was moved"
    assert not (result.data_dir / "config.env").exists(), "the wizard never writes keys"

    # Startup is what creates the key for the new data — a different one.
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=os.environ, program_dir=tmp_path
    )
    new_key = (ctx.data_dir / "config.env").read_text(encoding="utf-8")
    assert "CPM_SECRET_KEY=original-key" not in new_key, "fresh data gets its own key"
    assert "CPM_SECRET_KEY=" in new_key


def test_a_target_that_already_holds_data_is_never_overwritten(tmp_path, protected_env):
    seed_data(tmp_path / "Data", key="first-key")
    seed_data(tmp_path / "target", key="other-key")
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "target",
        browser_dir=tmp_path / "Browser",
        temp_dir=tmp_path / "Temp",
        browser_source="skip",
        data_choice="migrate",
    )
    with pytest.raises(portable.PortableError):
        wizard.apply_answers(tmp_path, answers, environ=os.environ)

    assert (tmp_path / "target" / "config.env").read_text(encoding="utf-8") == (
        "CPM_SECRET_KEY=other-key\n"
    ), "the target's own key must be untouched"
    assert (tmp_path / "Data" / "profiles.db").exists(), "the source stays too"


def test_a_bad_path_is_reported_before_anything_is_written(tmp_path, protected_env):
    blocker = tmp_path / "no"
    blocker.write_text("file")
    answers = wizard.WizardAnswers(
        data_dir=blocker,
        browser_dir=tmp_path / "Browser",
        temp_dir=tmp_path / "Temp",
        browser_source="skip",
    )
    with pytest.raises(portable.DataDirNotWritable):
        wizard.apply_answers(tmp_path, answers, environ=os.environ)
    assert not (tmp_path / "paths.env").exists()


def test_the_dialog_language_defaults_to_chinese():
    assert wizard.wizard_lang({"LANG": "zh_CN.UTF-8"}) == "zh-CN"
    assert wizard.wizard_lang({"LANG": "en_US.UTF-8"}) == "en"
    assert wizard.wizard_lang({}) == "zh-CN"


def test_the_rendered_dialog_carries_both_languages():
    html = wizard._WIZARD_HTML
    assert "__STRINGS__" in html and "__LANG__" in html
    assert "欢迎使用 Fingerprint Lite" in wizard._WIZARD_STRINGS["zh-CN"]["title"]
    assert wizard._WIZARD_STRINGS["en"]["start"] == "Start"
    assert wizard._WIZARD_STRINGS["zh-CN"]["start"] == "开始"
    assert set(wizard._WIZARD_STRINGS["zh-CN"]) == set(wizard._WIZARD_STRINGS["en"])


def test_a_second_start_while_setup_runs_is_refused(tmp_path):
    """Two concurrent installs would fight over the same Browser folder."""
    import threading

    release = threading.Event()
    state: dict = {}

    def slow():
        release.wait(timeout=10)
        return None

    first = wizard._start_work(state, slow, with_geoip=False)
    try:
        assert json.loads(first)["started"] is True
        second = wizard._start_work(state, slow, with_geoip=False)
        assert json.loads(second)["ok"] is False
    finally:
        release.set()
        state["worker"].join(timeout=10)


def test_a_worker_failure_is_reported_not_swallowed():
    """A setup crash lands in the progress snapshot the page polls."""
    from camoufox_pm import browser_env

    state: dict = {}

    def broken():
        raise portable.PortableError("disk went away")

    assert json.loads(wizard._start_work(state, broken, with_geoip=False))["started"] is True
    state["worker"].join(timeout=10)

    assert state["ok"] is False
    snapshot = browser_env.browser_progress()
    assert snapshot["stage"] == "failed"
    assert snapshot["error"] == "disk went away"
    browser_env.reset_browser_progress()


# ---------------------------------------------------------------------------
# The browser source
# ---------------------------------------------------------------------------


def test_offline_zip_installs_into_the_chosen_browser_folder(
    tmp_path, protected_env, monkeypatch, camoufox_constants_restored
):
    import hashlib
    import posixpath
    import zipfile

    from camoufox import pkgman

    # Same derivation as the product's launch_path() completeness check: the
    # fixture must carry the platform executable, not a hardcoded .exe.
    entry = pkgman.LAUNCH_FILE[pkgman.OS_NAME]
    if pkgman.OS_NAME == "mac":
        entry = posixpath.normpath(posixpath.join("Camoufox.app/Contents/Resources", entry))

    zips = tmp_path / "zips"
    zips.mkdir()
    zip_path = zips / "official.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(entry, b"payload")
    monkeypatch.setattr(
        browser_env, "PINNED_SHA256", hashlib.sha256(zip_path.read_bytes()).hexdigest()
    )

    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "Data",
        browser_dir=tmp_path / "Browser",
        temp_dir=tmp_path / "Temp",
        browser_source="zip",
        zip_path=zip_path,
    )
    result = wizard.apply_answers(tmp_path, answers, environ=os.environ)

    assert result.browser_path is not None
    assert result.browser_path.is_relative_to(tmp_path / "Browser" / "cache")
    assert browser_env.installed_path() == result.browser_path


def test_download_source_asks_for_the_download(
    tmp_path, protected_env, monkeypatch, camoufox_constants_restored
):
    asked = []
    monkeypatch.setattr(
        browser_env, "install_from_download", lambda **kw: asked.append(kw) or Path("x")
    )
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "Data",
        browser_dir=tmp_path / "Browser",
        temp_dir=tmp_path / "Temp",
        browser_source="download",
    )
    result = wizard.apply_answers(tmp_path, answers, environ=os.environ)
    assert asked and result.browser_path == Path("x")


def test_skip_installs_nothing(tmp_path, protected_env, monkeypatch, camoufox_constants_restored):
    monkeypatch.setattr(
        browser_env, "install_from_download", lambda **k: pytest.fail("no download")
    )
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "Data",
        browser_dir=tmp_path / "Browser",
        temp_dir=tmp_path / "Temp",
        browser_source="skip",
    )
    assert wizard.apply_answers(tmp_path, answers, environ=os.environ).browser_path is None


# ---------------------------------------------------------------------------
# Scripted answers (the automated test door) and the wizard result
# ---------------------------------------------------------------------------


def test_scripted_answers_run_without_a_window(
    tmp_path, protected_env, monkeypatch, camoufox_constants_restored
):
    answers = wizard.WizardAnswers(
        data_dir=tmp_path / "S" / "Data",
        browser_dir=tmp_path / "S" / "Browser",
        temp_dir=tmp_path / "S" / "Temp",
        browser_source="skip",
    )
    result = wizard.run_wizard(tmp_path, environ=os.environ, scripted=answers)
    assert result is not None
    assert result.data_dir == tmp_path / "S" / "Data"
    assert (tmp_path / "paths.env").exists()


def test_a_stray_environment_variable_never_skips_the_dialog(tmp_path, protected_env, monkeypatch):
    """Automation goes through an explicit flag; an env var is not a bypass."""
    monkeypatch.setenv(wizard.ANSWERS_ENV, '{"data_dir": "C:/somewhere"}')
    monkeypatch.chdir(tmp_path)
    shown = []
    monkeypatch.setattr(wizard, "_open_window", lambda *a, **k: shown.append(True) or None)

    assert wizard.should_show_wizard(tmp_path, ["FingerprintLite", "--desktop"], os.environ)
    wizard.run_wizard(tmp_path, environ=os.environ)
    assert shown, "the dialog must still be shown — the env var decides nothing"


def test_use_defaults_applies_the_program_root(
    tmp_path, protected_env, camoufox_constants_restored
):
    result = wizard.use_defaults(tmp_path, environ=os.environ, browser_source="skip")
    assert result.data_dir == tmp_path / "Data"
    assert result.browser_dir == tmp_path / "Browser"
    assert (tmp_path / "paths.env").exists(), "answered once — the dialog will not return"


def test_cancelling_leaves_everything_as_it_was(tmp_path, protected_env, monkeypatch):
    monkeypatch.setattr(wizard, "_open_window", lambda *a, **k: None)
    assert wizard.run_wizard(tmp_path, environ=os.environ) is None
    assert not (tmp_path / "paths.env").exists()


def test_the_real_window_passes_pywebview_an_object_with_exposed_methods(
    tmp_path, protected_env, monkeypatch
):
    """PyWebView discovers methods via dir(obj), not dictionary keys.

    A dict renders the page but leaves ``pywebview.api.submit`` undefined: the
    Start button changes to "Working…" and no path choice is ever applied.
    """
    captured = {}

    class FakeWindow:
        def destroy(self):
            pass

    class FakeWebview(types.ModuleType):
        FOLDER_DIALOG = 1
        OPEN_DIALOG = 2

        def __init__(self):
            super().__init__("webview")

        def create_file_dialog(self, *_args, **_kwargs):
            return None

        def create_window(self, _title, **kwargs):
            captured["js_api"] = kwargs["js_api"]
            captured["html"] = kwargs["html"]
            return FakeWindow()

        def start(self):
            pass

    monkeypatch.setitem(__import__("sys").modules, "webview", FakeWebview())

    wizard._open_window(tmp_path, environ=os.environ)

    api = captured["js_api"]
    for method in ("pick_folder", "pick_zip", "submit", "use_defaults", "progress", "cancel"):
        assert callable(getattr(api, method, None)), (
            f"PyWebView cannot expose {method}: js_api must be an object with bound methods"
        )

    html = captured["html"]
    assert "__STRINGS__" not in html and "__LANG__" not in html
    assert "Welcome to Fingerprint Lite" in html
    # Both dictionaries ride along as JSON (escaped); the page swaps them in.
    assert '"zh-CN"' in html and '"use_defaults"' in html
    assert "开始" in wizard._WIZARD_STRINGS["zh-CN"]["start"]


def test_folder_and_zip_pickers_use_the_created_window(tmp_path, protected_env, monkeypatch):
    """PyWebView 6 exposes file dialogs on Window, not on the module."""
    captured = {"dialogs": []}

    class FakeWindow:
        destroyed = False

        def create_file_dialog(self, dialog_type, **kwargs):
            captured["dialogs"].append((dialog_type, kwargs))
            return ("D:/picked",)

        def destroy(self):
            self.destroyed = True

    class FakeWebview(types.ModuleType):
        FOLDER_DIALOG = 20
        OPEN_DIALOG = 10

        def __init__(self):
            super().__init__("webview")
            self.window = FakeWindow()

        def create_file_dialog(self, *_args, **_kwargs):
            pytest.fail("file dialogs belong to the created Window instance")

        def create_window(self, _title, **kwargs):
            captured["js_api"] = kwargs["js_api"]
            return self.window

        def start(self):
            pass

    fake = FakeWebview()
    monkeypatch.setitem(__import__("sys").modules, "webview", fake)

    wizard._open_window(tmp_path, environ=os.environ)
    api = captured["js_api"]
    assert api.pick_folder("data") == "D:/picked"
    assert api.pick_zip() == "D:/picked"
    assert captured["dialogs"] == [
        (fake.FOLDER_DIALOG, {}),
        (fake.OPEN_DIALOG, {"file_types": ("ZIP archives (*.zip)",)}),
    ]
