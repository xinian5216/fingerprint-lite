"""Portable mode: fixed Data folder, persistent config, redacted file logs, one instance.

The portable edition must keep user data, SQLite, config and logs under one
explicit ``Data/`` folder beside the program, fail loudly when that folder is
not usable (never silently relocate), write logs with rotation and redaction,
and refuse a second start while one instance is live.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from camoufox_pm import portable

# ---------------------------------------------------------------------------
# Activation and location
# ---------------------------------------------------------------------------


def test_plain_source_runs_are_not_portable(tmp_path):
    """Source checkouts keep today's behaviour unless portable mode is asked for."""
    assert (
        portable.bootstrap(["camoufox-pm"], frozen=False, environ={}, program_dir=tmp_path) is None
    )


def test_a_frozen_exe_uses_the_data_folder_beside_the_program(tmp_path):
    ctx = portable.bootstrap(["FingerprintLite"], frozen=True, environ={}, program_dir=tmp_path)
    assert ctx is not None
    assert ctx.data_dir == tmp_path / "Data"
    assert ctx.profiles_dir == ctx.data_dir / "profiles"
    assert ctx.logs_dir == ctx.data_dir / "logs"


def test_the_portable_flag_activates_portable_mode(tmp_path):
    ctx = portable.bootstrap(
        ["camoufox-pm", "--portable"], frozen=False, environ={}, program_dir=tmp_path
    )
    assert ctx is not None
    assert ctx.data_dir == tmp_path / "Data"


def test_cpm_data_dir_env_moves_the_data_folder(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    ctx = portable.bootstrap(
        ["camoufox-pm"],
        frozen=False,
        environ={"CPM_DATA_DIR": str(elsewhere)},
        program_dir=tmp_path,
    )
    assert ctx is not None
    assert ctx.data_dir == elsewhere


def test_the_program_dir_comes_from_the_exe_when_frozen(tmp_path, monkeypatch):
    exe = tmp_path / "app" / "FingerprintLite.exe"
    assert portable.resolve_program_dir(frozen=True, executable=str(exe)) == exe.parent
    monkey_cwd = tmp_path / "somewhere"
    monkey_cwd.mkdir()
    monkeypatch.chdir(monkey_cwd)
    assert portable.resolve_program_dir(frozen=False, executable=str(exe)) == monkey_cwd


# ---------------------------------------------------------------------------
# Data folder preparation
# ---------------------------------------------------------------------------


def test_prepare_data_dir_creates_the_layout(tmp_path):
    data_dir = tmp_path / "Data"
    portable.prepare_data_dir(data_dir)
    assert (data_dir / "profiles").is_dir()
    assert (data_dir / "logs").is_dir()


def test_an_unusable_data_folder_is_a_clear_error_not_a_relocation(tmp_path):
    """A file where the Data folder should be: fail, name the path, move nothing."""
    blocker = tmp_path / "Data"
    blocker.write_text("not a folder")
    with pytest.raises(portable.DataDirNotWritable) as excinfo:
        portable.prepare_data_dir(blocker)
    message = str(excinfo.value)
    assert str(blocker) in message
    assert excinfo.value.path == blocker
    # No fallback location was created, and the blocker is untouched.
    assert blocker.read_text() == "not a folder"


def test_bootstrap_reports_the_data_folder_error_and_keeps_the_path(tmp_path):
    blocker = tmp_path / "Data"
    blocker.write_text("not a folder")
    with pytest.raises(portable.DataDirNotWritable) as excinfo:
        portable.bootstrap(["FingerprintLite"], frozen=True, environ={}, program_dir=tmp_path)
    assert excinfo.value.path == blocker


def test_fatal_errors_reach_the_user_even_without_a_console(monkeypatch):
    """The windowed entry must never fail silently: a dialog, not nothing."""
    shown = []
    monkeypatch.setattr(portable, "_show_error_box", lambda title, message: shown.append(message))
    wanted = Path("X:/FingerprintLite/Data")
    portable.notify_fatal(
        portable.DataDirNotWritable(wanted, OSError("denied")),
        force_box=True,
    )
    assert shown and str(wanted) in shown[0]


# ---------------------------------------------------------------------------
# Persistent config and secret key
# ---------------------------------------------------------------------------


def test_the_secret_key_is_generated_once_and_reused(tmp_path):
    environ: dict[str, str] = {}
    first = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=environ, program_dir=tmp_path
    )
    key = environ["CPM_SECRET_KEY"]
    Fernet(key.encode())  # must be a well-formed Fernet key
    assert first is not None

    second_environ: dict[str, str] = {}
    portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=second_environ, program_dir=tmp_path
    )
    assert second_environ["CPM_SECRET_KEY"] == key, "a restart must decrypt what it encrypted"


def test_a_key_from_the_environment_is_used_as_is(tmp_path):
    environ = {"CPM_SECRET_KEY": "user-supplied"}
    portable.bootstrap(["FingerprintLite"], frozen=True, environ=environ, program_dir=tmp_path)
    assert environ["CPM_SECRET_KEY"] == "user-supplied"


def test_the_database_lives_in_the_data_folder(tmp_path, monkeypatch):
    # Tracked first so teardown restores the real environment however this ends.
    monkeypatch.setenv("CPM_DB_PATH", "sentinel")
    monkeypatch.setenv("CPM_SECRET_KEY", "sentinel")
    environ = os.environ
    ctx = portable.bootstrap(
        ["FingerprintLite"], frozen=True, environ=environ, program_dir=tmp_path
    )
    assert environ["CPM_DB_PATH"] == str(ctx.data_dir / "profiles.db")

    from camoufox_pm.config import get_settings

    try:
        assert Path(get_settings().db_path) == ctx.data_dir / "profiles.db"
    finally:
        get_settings.cache_clear()


def test_config_file_holds_the_key_but_environment_values_are_not_auto_persisted(tmp_path):
    """Data/config.env is the documented place to edit config; one-off values stay one-off."""
    first = {"CPM_PORT": "9123"}
    portable.bootstrap(["FingerprintLite"], frozen=True, environ=first, program_dir=tmp_path)
    assert first["CPM_PORT"] == "9123", "a real environment value is honoured"
    config_text = (tmp_path / "Data" / "config.env").read_text(encoding="utf-8")
    assert "CPM_SECRET_KEY=" in config_text
    assert "CPM_PORT" not in config_text, "a one-off value must not become permanent config"

    second: dict[str, str] = {}
    portable.bootstrap(["FingerprintLite"], frozen=True, environ=second, program_dir=tmp_path)
    assert second.get("CPM_PORT") is None, "only managed keys are injected"


# ---------------------------------------------------------------------------
# Logs: rotation and redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,hidden",
    [
        ("connecting with password=hunter2secret", "hunter2secret"),
        ("proxy http://user:s3cr3tvalue@host:8080", "s3cr3tvalue"),
        ("X-API-Key: tok_abcdefghij_value", "tok_abcdefghij_value"),
        ("stored enc:gAAAAABhZ_notarealtoken", "gAAAAABhZ_notarealtoken"),
        ("CPM_SECRET_KEY=supersecretvalue", "supersecretvalue"),
    ],
)
def test_redaction_masks_sensitive_shapes(text, hidden):
    assert hidden in text, "fixture sanity"
    assert hidden not in portable.redact(text)


def test_redaction_leaves_plain_text_alone():
    assert portable.redact("launched profile abc-123 on port 8000") == (
        "launched profile abc-123 on port 8000"
    )


def test_file_logging_writes_to_data_logs_and_redacts(tmp_path):
    from loguru import logger

    ctx = portable.bootstrap(["FingerprintLite"], frozen=True, environ={}, program_dir=tmp_path)
    sink_id = portable.install_file_logging(ctx)
    try:
        logger.info("proxy password=hunter2secret is set")
        logger.info("launched profile abc-123")
    finally:
        logger.remove(sink_id)
        portable.uninstall_redaction()

    log = (ctx.logs_dir / "app.log").read_text(encoding="utf-8")
    assert "launched profile abc-123" in log
    assert "hunter2secret" not in log


def test_the_file_sink_rotates_by_size(tmp_path):
    from loguru import logger

    ctx = portable.bootstrap(["FingerprintLite"], frozen=True, environ={}, program_dir=tmp_path)
    sink_id = portable.install_file_logging(ctx, rotation_bytes=256, retention=1)
    try:
        for _ in range(40):
            logger.warning("x" * 100)
    finally:
        logger.remove(sink_id)
        portable.uninstall_redaction()

    assert (ctx.logs_dir / "app.log").exists()
    rotated = [p for p in ctx.logs_dir.glob("app*.log") if p.name != "app.log"]
    assert rotated, "exceeding the size cap must roll the log over"
    assert len(rotated) <= 1, "retention=1 keeps one rolled file"


def test_console_capture_rotates_and_redacts(tmp_path):
    capture = portable.ConsoleCapture(tmp_path / "console.log", rotation_bytes=256, retention=1)
    capture.write("password=verysecretvalue\n")
    for _ in range(10):
        capture.write("y" * 200 + "\n")
    capture.close()
    everything = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("console*.log"))
    assert "verysecretvalue" not in everything, "rolled files are redacted too"
    assert "yyyy" in everything
    assert (tmp_path / "console.1.log").exists(), "rotation must roll the capture file"


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------


def test_a_live_instance_stops_a_second_start(tmp_path):
    ctx = portable.bootstrap(["FingerprintLite"], frozen=True, environ={}, program_dir=tmp_path)
    lock = portable.acquire_instance_lock(ctx, port=8123)
    try:
        with pytest.raises(portable.InstanceAlreadyRunning) as excinfo:
            portable.acquire_instance_lock(ctx, port=8123)
        assert str(os.getpid()) in str(excinfo.value)
    finally:
        lock.release()
    assert not (ctx.data_dir / "instance.lock").exists()


def test_a_stale_lock_is_reclaimed(tmp_path):
    ctx = portable.bootstrap(["FingerprintLite"], frozen=True, environ={}, program_dir=tmp_path)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    stale_pid = proc.pid
    (ctx.data_dir / "instance.lock").write_text(
        json.dumps({"pid": stale_pid, "port": 8123, "exe": "old"})
    )

    lock = portable.acquire_instance_lock(ctx, port=8123)
    try:
        stored = json.loads((ctx.data_dir / "instance.lock").read_text())
        assert stored["pid"] == os.getpid()
    finally:
        lock.release()
