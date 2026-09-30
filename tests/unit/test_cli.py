"""The console script: what the flags do before the server starts.

Nothing here binds a port or opens a window — uvicorn, the browser timer and
desktop mode are all stood in for. What is checked is the part with its own
logic: the flags becoming the settings the rest of the app reads, and the URL a
browser is pointed at.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from camoufox_pm import cli
from camoufox_pm.config import get_settings


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Run ``main()`` with the given arguments, capturing what it would start."""
    from camoufox import geolocation, multiversion, pkgman

    from camoufox_pm import browser_env, desktop, portable

    monkeypatch.chdir(tmp_path)
    for key in (*portable.PATH_KEYS, "CPM_DB_PATH", "CPM_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(portable, "_windowed", False)
    monkeypatch.setattr(portable, "_notified", False)
    monkeypatch.setattr(portable, "_ACTIVE", False)

    started: dict = {"uvicorn": None, "timers": [], "opened": [], "desktop": None}

    class FakeTimer:
        def __init__(self, delay, function):
            started["timers"].append((delay, function))

        def start(self):
            """Deliberately does nothing: the callback is invoked by the test."""

    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: started.update(uvicorn=kw))
    monkeypatch.setattr(cli.threading, "Timer", FakeTimer)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: started["opened"].append(url))
    monkeypatch.setattr(desktop, "run_desktop", lambda **kw: started.update(desktop=kw))

    # A portable start asks for the pinned browser in a background thread. A
    # real preparation downloads ~470 MB into whatever browser folder the run
    # resolves — the repository root, for a test that does not chdir. Record
    # the calls instead; the wiring has its own test below.
    started["browser_prep"] = []
    monkeypatch.setattr(
        browser_env,
        "ensure_in_background",
        lambda **kwargs: started["browser_prep"].append(kwargs),
    )
    # A portable run re-roots camoufox's paths; put them back after the run so
    # one test cannot decide where the next one looks for a browser.
    saved_constants = {
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
    saved_browser_dir = browser_env._BROWSER_DIR

    def restore_browser_paths() -> None:
        for (module, name), value in saved_constants.items():
            setattr(module, name, value)
        browser_env._BROWSER_DIR = saved_browser_dir

    # main() writes these itself; setting them through monkeypatch first means
    # the originals are restored however the run ends.
    monkeypatch.setenv("CPM_HOST", "127.0.0.1")
    monkeypatch.setenv("CPM_PORT", "8000")

    def invoke(*args: str):
        monkeypatch.setattr(sys, "argv", ["camoufox-pm", *args])
        get_settings.cache_clear()
        try:
            cli.main()
        finally:
            restore_browser_paths()
        return started

    yield invoke
    get_settings.cache_clear()


def test_the_flags_become_the_settings_the_rest_of_the_app_reads(run):
    """The Settings screen, CORS and the logs all read get_settings(). Leaving it
    on the defaults would have them report an address nothing is listening on."""
    started = run("--host", "0.0.0.0", "--port", "9123", "--no-browser")

    assert get_settings().host == "0.0.0.0"
    assert get_settings().port == 9123
    assert started["uvicorn"]["host"] == "0.0.0.0"
    assert started["uvicorn"]["port"] == 9123


def test_no_browser_opens_nothing(run):
    started = run("--no-browser")

    assert started["timers"] == []


def test_a_wildcard_bind_is_opened_as_localhost(run):
    """A browser cannot fetch http://0.0.0.0/ — the tab just fails."""
    started = run("--host", "0.0.0.0", "--port", "9123")

    assert len(started["timers"]) == 1
    _delay, open_the_browser = started["timers"][0]
    open_the_browser()

    assert started["opened"] == ["http://localhost:9123/"]


def test_a_real_address_is_opened_as_itself(run):
    started = run("--host", "192.168.1.5", "--port", "9123")

    started["timers"][0][1]()

    assert started["opened"] == ["http://192.168.1.5:9123/"]


def test_desktop_mode_hands_over_instead_of_serving_here(run, tmp_path, monkeypatch):
    """run_desktop starts its own server; starting a second one would take the port."""
    from camoufox_pm import desktop, wizard

    asked: list[dict] = []
    monkeypatch.setattr(desktop, "run_desktop", lambda **kw: asked.append(kw))
    monkeypatch.chdir(tmp_path)  # a clean program root: no first-start wizard here
    monkeypatch.setattr(wizard, "should_show_wizard", lambda *a, **k: False)

    started = run("--desktop", "--port", "9123")

    assert asked == [{"host": "127.0.0.1", "port": 9123, "storage_path": None}]
    assert started["uvicorn"] is None
    assert started["timers"] == []


# --- `camoufox-pm user ...` ---------------------------------------------------


@pytest.fixture
def run_user(monkeypatch, tmp_path):
    """Run a ``user`` subcommand against a throwaway database, with password
    prompts answered from a scripted list."""

    from camoufox_pm import portable

    monkeypatch.chdir(tmp_path)
    for key in portable.PATH_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))

    def invoke(*args: str, prompts: list[str] | None = None):
        answers = list(prompts or [])
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": answers.pop(0))
        monkeypatch.setattr(sys, "argv", ["camoufox-pm", "user", *args])
        get_settings.cache_clear()
        try:
            cli.main()
        finally:
            get_settings.cache_clear()

    yield invoke


async def _usernames(tmp_path) -> list[str]:
    from camoufox_pm.core.database import StorageManager

    storage = StorageManager(str(tmp_path / "cli.db"))
    await storage.initialize()
    try:
        return [user["username"] for user in await storage.list_users()]
    finally:
        await storage.close()


def test_user_add_creates_the_account(run_user, tmp_path):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])

    assert asyncio.run(_usernames(tmp_path)) == ["alice"]


def test_user_add_rejects_a_short_password(run_user, tmp_path):
    with pytest.raises(SystemExit):
        run_user("add", "alice", prompts=["short", "short"])

    assert asyncio.run(_usernames(tmp_path)) == []


def test_user_add_rejects_mismatched_prompts(run_user, tmp_path):
    with pytest.raises(SystemExit):
        run_user("add", "alice", prompts=["one-password-8", "another-password"])


def test_user_add_refuses_a_duplicate(run_user):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    with pytest.raises(SystemExit):
        run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])


def test_user_remove_deletes_the_account(run_user, tmp_path):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    run_user("remove", "alice")

    assert asyncio.run(_usernames(tmp_path)) == []


def test_user_list_shows_names_and_never_hashes(run_user, capsys):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    run_user("list")

    out = capsys.readouterr().out
    assert "alice" in out
    assert "argon2" not in out
    assert "hunter22-long" not in out


def test_user_passwd_changes_the_password(run_user, tmp_path):
    from camoufox_pm.core import auth
    from camoufox_pm.core.database import StorageManager

    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    run_user("passwd", "alice", prompts=["new-password-9", "new-password-9"])

    async def check() -> bool:
        storage = StorageManager(str(tmp_path / "cli.db"))
        await storage.initialize()
        try:
            user = await storage.get_user_by_username("alice")
            assert user is not None
            return auth.verify_password(user["password_hash"], "new-password-9")
        finally:
            await storage.close()

    assert asyncio.run(check())


# ---------------------------------------------------------------------------
# Portable mode wiring
# ---------------------------------------------------------------------------


def _protect_portable_env(monkeypatch, data_dir):

    monkeypatch.setenv("CPM_DATA_DIR", str(data_dir))
    # Tracked first so teardown restores the real environment however this ends.
    monkeypatch.setenv("CPM_DB_PATH", "sentinel")
    monkeypatch.setenv("CPM_SECRET_KEY", "sentinel")
    # ...but absent for the run itself: a first start has no key yet, so the
    # portable layer generates one and keeps it in Data/config.env.
    os.environ.pop("CPM_SECRET_KEY", None)


def test_portable_mode_wires_the_data_folder_and_releases_the_lock(run, tmp_path, monkeypatch):

    data_dir = tmp_path / "Data"
    _protect_portable_env(monkeypatch, data_dir)

    run("--no-browser")

    assert (data_dir / "profiles").is_dir()
    assert (data_dir / "logs").is_dir()
    assert (data_dir / "config.env").exists()
    assert os.environ["CPM_DB_PATH"] == str(data_dir / "profiles.db")
    assert not (data_dir / "instance.lock").exists(), "the lock must not outlive the run"


def test_the_portable_flag_is_accepted_in_source_runs(run, tmp_path, monkeypatch):

    monkeypatch.setenv("CPM_DB_PATH", "sentinel")
    monkeypatch.setenv("CPM_SECRET_KEY", "sentinel")
    monkeypatch.chdir(tmp_path)

    run("--portable", "--no-browser")

    assert (tmp_path / "Data" / "profiles").is_dir()
    assert os.environ["CPM_DB_PATH"] == str(tmp_path / "Data" / "profiles.db")


def test_a_second_start_is_refused_with_a_message_instead_of_a_second_server(
    run, tmp_path, monkeypatch, capsys
):

    from camoufox_pm import portable

    data_dir = tmp_path / "Data"
    _protect_portable_env(monkeypatch, data_dir)
    ctx = portable.bootstrap(["camoufox-pm"], environ=os.environ, program_dir=tmp_path)
    # This holder is the test process itself. Some sandbox /proc mounts expose
    # host PIDs, so psutil cannot resolve the process's namespace-local PID.
    monkeypatch.setattr(portable, "_pid_alive", lambda pid: pid == os.getpid())
    lock = portable.acquire_instance_lock(ctx, port=8123)
    try:
        with pytest.raises(SystemExit) as excinfo:
            run("--no-browser", "--port", "8123")
        assert excinfo.value.code == 0, "being already running is not a crash"
    finally:
        lock.release()
    assert "already running" in capsys.readouterr().err


def test_an_unusable_data_folder_exits_with_a_clear_error(run, tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "Data"
    blocker.write_text("not a folder")
    monkeypatch.setenv("CPM_DATA_DIR", str(blocker))

    with pytest.raises(SystemExit) as excinfo:
        run("--no-browser")

    assert excinfo.value.code == 2
    assert "data folder is not usable" in capsys.readouterr().err


def test_startup_prepares_the_pinned_browser_in_the_background(run, tmp_path, monkeypatch):
    """The portable start readies the pinned browser without blocking the UI."""
    from camoufox_pm import browser_env

    asked = []
    monkeypatch.setattr(browser_env, "ensure_in_background", lambda **kw: asked.append(kw))
    _protect_portable_env(monkeypatch, tmp_path / "Data")

    run("--no-browser")

    assert asked, "a portable start must prepare the pinned browser"


def test_portable_runs_do_not_prepare_the_browser_themselves(run, tmp_path, monkeypatch):
    """The preparation is stubbed, and the paths it would move are put back."""
    from camoufox import multiversion, pkgman

    from camoufox_pm import browser_env

    def refuse(**_kwargs):
        raise AssertionError("a test must not download the browser")

    monkeypatch.setattr(browser_env, "install_from_download", refuse)
    _protect_portable_env(monkeypatch, tmp_path / "Data")
    install_before = pkgman.INSTALL_DIR
    browsers_before = multiversion.BROWSERS_DIR
    root_before = browser_env._BROWSER_DIR

    started = run("--no-browser")

    assert started["browser_prep"], "a portable start must ask for the preparation"
    assert pkgman.INSTALL_DIR == install_before, "the run leaked camoufox's install dir"
    assert multiversion.BROWSERS_DIR == browsers_before, "the run leaked the browsers dir"
    assert browser_env._BROWSER_DIR == root_before, "the run leaked its browser root"


# ---------------------------------------------------------------------------
# The first-start wizard wiring
# ---------------------------------------------------------------------------


def test_the_wizard_runs_before_startup_on_a_first_desktop_start(run, tmp_path, monkeypatch):
    from camoufox_pm import desktop, wizard

    monkeypatch.chdir(tmp_path)  # a program root with nothing in it yet
    asked = []
    monkeypatch.setattr(
        wizard,
        "run_wizard",
        lambda *a, **k: (
            asked.append(k)
            or wizard.WizardResult(
                data_dir=tmp_path / "Data",
                browser_dir=tmp_path / "Browser",
                temp_dir=tmp_path / "Temp",
            )
        ),
    )
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: None)

    run("--desktop", "--port", "9123")

    assert asked, "a first desktop start must offer the wizard"


def test_cancelling_the_wizard_stops_the_program(run, tmp_path, monkeypatch):
    """Cancel is not 'use the defaults' — it is the door out, quietly."""
    from camoufox_pm import desktop, portable, wizard

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: pytest.fail("no startup after cancel"))
    monkeypatch.setattr(
        portable, "_show_error_box", lambda *a, **k: pytest.fail("cancel is not an error dialog")
    )

    with pytest.raises(SystemExit) as excinfo:
        run("--desktop", "--port", "9123")
    assert excinfo.value.code == 0


def test_cli_and_server_runs_do_not_open_the_wizard(run, tmp_path, monkeypatch):
    from camoufox_pm import wizard

    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: pytest.fail("no wizard here"))
    run("--no-browser")


# ---------------------------------------------------------------------------
# First-run handoff: after the wizard, the manager must actually appear
# ---------------------------------------------------------------------------


def _completed_first_run(program_root, *, paths_written=True):
    """A wizard answer that wrote paths.env, the way a real first run ends."""
    from camoufox_pm import wizard as wizard_module

    return wizard_module.WizardResult(
        data_dir=program_root / "Data",
        browser_dir=program_root / "Browser",
        temp_dir=program_root / "Temp",
        paths_written=paths_written,
    )


def _spawned(monkeypatch, record):
    """Record what would be relaunched instead of actually starting anything."""
    import subprocess

    class FakePopen:
        pid = 4242

        def __init__(self, command, **kwargs):
            record.append({"command": list(command), "cwd": kwargs.get("cwd")})

    monkeypatch.setattr(subprocess, "Popen", FakePopen)


def test_a_completed_windowed_first_run_relaunches_instead_of_a_second_loop(
    run, tmp_path, monkeypatch
):
    """The manager must appear: one GUI loop per process, not two in one.

    The second webview.start() in the same process never produced a window,
    so the wizard process hands the completed setup to a fresh process whose
    paths.env now exists — that one skips the dialog and runs the desktop.
    """
    from camoufox_pm import desktop, portable, wizard

    monkeypatch.chdir(tmp_path)
    (tmp_path / "FingerprintLite.exe").write_bytes(b"packaged")

    def complete(*a, **k):
        assert not (tmp_path / "paths.env").exists()
        assert not (tmp_path / "Data" / "instance.lock").exists()
        (tmp_path / "paths.env").write_text(
            "CPM_DATA_DIR=" + str(tmp_path / "Data") + "\n", encoding="utf-8"
        )
        return _completed_first_run(tmp_path)

    monkeypatch.setattr(wizard, "run_wizard", complete)
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: pytest.fail("no second GUI loop"))
    monkeypatch.setattr(portable, "bootstrap", lambda: pytest.fail("parent must not bootstrap"))
    monkeypatch.setattr(
        portable,
        "resolve_program_exe",
        lambda program_dir=None: Path(program_dir) / "FingerprintLite.exe",
    )
    monkeypatch.setattr(portable, "is_windowed", lambda: True)
    monkeypatch.setattr(portable, "is_frozen", lambda: True)
    monkeypatch.setattr(portable, "resolve_program_dir", lambda *a, **k: tmp_path)
    spawned = []
    _spawned(monkeypatch, spawned)

    with pytest.raises(SystemExit) as excinfo:
        run("--desktop", "--port", "9123")
    assert excinfo.value.code == 0

    assert len(spawned) == 1, "the manager must be started exactly once"
    assert spawned == [
        {
            "command": [str(tmp_path / "FingerprintLite.exe"), "--desktop", "--port", "9123"],
            "cwd": str(tmp_path),
        }
    ]


def test_the_relaunch_skips_the_wizard_and_enters_the_desktop_path(run, tmp_path, monkeypatch):
    """The child's own start must see paths.env and land on the desktop loop.

    Same process here (the relaunch decision is what this pins): a second
    start against the program root now finds paths.env, so the wizard is not
    offered and ``run_desktop`` is where the flow ends.
    """
    from camoufox_pm import desktop, portable, wizard

    monkeypatch.chdir(tmp_path)
    (tmp_path / "paths.env").write_text(
        "CPM_DATA_DIR=" + str(tmp_path / "Data") + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: pytest.fail("wizard must be skipped"))
    monkeypatch.setattr(portable, "is_windowed", lambda: True)
    monkeypatch.setattr(portable, "is_frozen", lambda: True)
    monkeypatch.setattr(portable, "resolve_program_dir", lambda *a, **k: tmp_path)
    _protect_portable_env(monkeypatch, tmp_path / "Data")
    entered = []

    def manager(**kwargs):
        assert (tmp_path / "Data" / "instance.lock").exists()
        entered.append(kwargs)

    monkeypatch.setattr(desktop, "run_desktop", manager)

    run("--desktop", "--port", "9123")

    assert entered, "the relaunched start must reach the desktop path"
    assert not (tmp_path / "Data" / "instance.lock").exists()


@pytest.mark.parametrize(
    "setup_args",
    [["--wizard"], ["--wizard-answers", "answers.json"], ["--wizard-answers=answers.json"]],
)
def test_relaunch_consumes_setup_flags_and_preserves_runtime_args(
    tmp_path, monkeypatch, setup_args
):
    (tmp_path / "FingerprintLite.exe").write_bytes(b"packaged")
    (tmp_path / "paths.env").write_text("CPM_DATA_DIR=Data\n", encoding="utf-8")
    spawned = []
    _spawned(monkeypatch, spawned)
    with pytest.raises(SystemExit) as excinfo:
        cli._relaunch_for_manager(
            tmp_path, ["--desktop", *setup_args, "--port", "9123", "--portable"]
        )
    assert excinfo.value.code == 0
    assert spawned == [
        {
            "command": [
                str(tmp_path / "FingerprintLite.exe"),
                "--desktop",
                "--port",
                "9123",
                "--portable",
            ],
            "cwd": str(tmp_path),
        }
    ]


def test_explicit_setup_with_existing_paths_still_relaunches(run, tmp_path, monkeypatch):
    from camoufox_pm import portable, wizard

    (tmp_path / "FingerprintLite.exe").write_bytes(b"packaged")
    (tmp_path / "paths.env").write_text("CPM_DATA_DIR=Data\n", encoding="utf-8")
    result = _completed_first_run(tmp_path, paths_written=False)
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: result)
    monkeypatch.setattr(portable, "is_windowed", lambda: True)
    monkeypatch.setattr(portable, "is_frozen", lambda: True)
    monkeypatch.setattr(portable, "resolve_program_dir", lambda: tmp_path)
    spawned = []
    _spawned(monkeypatch, spawned)
    with pytest.raises(SystemExit) as excinfo:
        run("--desktop", "--wizard")
    assert excinfo.value.code == 0
    assert spawned[0]["command"] == [str(tmp_path / "FingerprintLite.exe"), "--desktop"]


@pytest.mark.parametrize("missing", ["FingerprintLite.exe", "paths.env"])
def test_incomplete_handoff_fails_instead_of_starting_a_second_loop(tmp_path, monkeypatch, missing):
    from camoufox_pm import portable

    for name in ("FingerprintLite.exe", "paths.env"):
        if name != missing:
            (tmp_path / name).write_text("fixture", encoding="utf-8")
    spawned, notified = [], []
    _spawned(monkeypatch, spawned)
    monkeypatch.setattr(portable, "notify_fatal", notified.append)
    with pytest.raises(SystemExit) as excinfo:
        cli._relaunch_for_manager(tmp_path, [])
    assert excinfo.value.code == 3
    assert notified and not spawned


def test_spawn_failure_is_reported_and_preserves_setup(tmp_path, monkeypatch):
    import subprocess

    from camoufox_pm import portable

    for name in ("FingerprintLite.exe", "paths.env"):
        (tmp_path / name).write_text("fixture", encoding="utf-8")

    def fail(*a, **k):
        raise OSError("synthetic launch failure")

    notified = []
    monkeypatch.setattr(subprocess, "Popen", fail)
    monkeypatch.setattr(portable, "notify_fatal", notified.append)
    with pytest.raises(SystemExit) as excinfo:
        cli._relaunch_for_manager(tmp_path, [])
    assert excinfo.value.code == 3
    assert notified and (tmp_path / "paths.env").read_text(encoding="utf-8") == "fixture"


def test_an_existing_paths_env_start_opens_the_manager_inline(run, tmp_path, monkeypatch):
    """Nothing changes for a normal start: wizard skipped, one desktop loop."""
    from camoufox_pm import desktop, wizard

    monkeypatch.chdir(tmp_path)
    (tmp_path / "paths.env").write_text(
        "CPM_DATA_DIR=" + str(tmp_path / "Data") + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: pytest.fail("no wizard here"))
    entered = []
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: entered.append(k))

    run("--desktop", "--port", "9123")

    assert entered, "the manager must open in-process as before"


def test_cancelling_the_wizard_never_relaunches(run, tmp_path, monkeypatch):
    from camoufox_pm import desktop, wizard

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: pytest.fail("no startup after cancel"))
    spawned = []
    _spawned(monkeypatch, spawned)

    with pytest.raises(SystemExit) as excinfo:
        run("--desktop", "--port", "9123")
    assert excinfo.value.code == 0
    assert spawned == [], "cancel means the user left — nothing may restart"


def test_a_failed_first_run_never_opens_the_manager(run, tmp_path, monkeypatch):
    from camoufox_pm import browser_env, desktop, wizard

    monkeypatch.chdir(tmp_path)

    def boom(*_a, **_k):
        raise browser_env.BrowserInstallError("no browser for you (synthetic)")

    monkeypatch.setattr(wizard, "run_wizard", boom)
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: pytest.fail("no manager after failure"))
    spawned = []
    _spawned(monkeypatch, spawned)

    with pytest.raises(SystemExit) as excinfo:
        run("--desktop", "--port", "9123")
    assert excinfo.value.code == 2
    assert spawned == [], "a failed setup must not start the manager"


def test_a_console_first_run_does_not_relaunch(run, tmp_path, monkeypatch):
    """Only the windowed entry relaunches; a console run keeps its flow."""

    answers = {
        "data_dir": str(tmp_path / "Data"),
        "browser_dir": str(tmp_path / "Browser"),
        "temp_dir": str(tmp_path / "Temp"),
        "browser_source": "skip",
    }
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps(answers), encoding="utf-8")
    (tmp_path / "FingerprintLite.exe").write_bytes(b"packaged")
    _protect_portable_env(monkeypatch, tmp_path / "Data")
    spawned = []
    _spawned(monkeypatch, spawned)

    run("--desktop", "--port", "9123", "--wizard-answers", str(answers_file))

    assert (tmp_path / "paths.env").exists(), "the scripted first run still applies"
    assert spawned == [], "a console run never relaunches into the manager"


def test_windowed_source_run_does_not_spawn_a_sibling_exe(run, tmp_path, monkeypatch):
    from camoufox_pm import portable, wizard

    (tmp_path / "FingerprintLite.exe").write_bytes(b"packaged")
    monkeypatch.setattr(portable, "is_windowed", lambda: True)
    monkeypatch.setattr(portable, "is_frozen", lambda: False)
    monkeypatch.setattr(wizard, "run_wizard", lambda *a, **k: _completed_first_run(tmp_path))
    spawned = []
    _spawned(monkeypatch, spawned)
    started = run("--desktop")
    assert not spawned
    assert started["desktop"] is not None


def test_scripted_answers_are_applied_before_the_backend_starts(run, tmp_path, monkeypatch):
    import os

    from camoufox_pm import desktop

    monkeypatch.chdir(tmp_path)  # the wizard records paths.env in the program root
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: None)
    answers = {
        "data_dir": str(tmp_path / "chosen" / "Data"),
        "browser_dir": str(tmp_path / "chosen" / "Browser"),
        "temp_dir": str(tmp_path / "chosen" / "Temp"),
        "browser_source": "skip",
    }
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps(answers), encoding="utf-8")
    _protect_portable_env(monkeypatch, tmp_path / "chosen" / "Data")

    run("--desktop", "--port", "9123", "--wizard-answers", str(answers_file))

    assert os.environ["CPM_DB_PATH"] == str(tmp_path / "chosen" / "Data" / "profiles.db")
    assert (tmp_path / "chosen" / "Data" / "profiles").is_dir()
    assert (tmp_path / "paths.env").exists()


def test_a_stray_env_var_is_not_a_way_to_skip_the_dialog(run, tmp_path, monkeypatch):
    """Automation needs --wizard-answers; ambient environment decides nothing."""
    from camoufox_pm import desktop, wizard

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CPM_WIZARD_ANSWERS_JSON", '{"data_dir": "E:/somewhere"}')
    shown = []
    monkeypatch.setattr(
        wizard,
        "run_wizard",
        lambda *a, **k: (
            shown.append(k)
            or wizard.WizardResult(
                data_dir=tmp_path / "Data",
                browser_dir=tmp_path / "Browser",
                temp_dir=tmp_path / "Temp",
            )
        ),
    )
    monkeypatch.setattr(desktop, "run_desktop", lambda **k: None)

    run("--desktop", "--port", "9123")

    assert shown, "the dialog must still appear — the env var must not answer it"
