"""The windowed entry: no console to fail into, so errors must surface loudly.

``FingerprintLite.exe`` is a windowed build — it has no console at all. Without
care that makes every startup failure silent. This entry captures all output
into ``Data/logs/console.log`` (redacted, rotated), forces desktop mode, and
turns any startup failure into a native error dialog plus a non-zero exit.
"""

import sys
from pathlib import Path

import pytest

from camoufox_pm import portable, windowed


@pytest.fixture
def windowed_env(tmp_path, monkeypatch):
    """A throwaway Data folder and a captured dialog channel."""
    monkeypatch.setenv("CPM_DATA_DIR", str(tmp_path / "Data"))
    shown = []
    monkeypatch.setattr(portable, "_show_error_box", lambda title, message: shown.append(message))
    monkeypatch.setattr(portable, "_windowed", False)
    monkeypatch.setattr(portable, "_notified", False)
    monkeypatch.setattr(sys, "argv", ["FingerprintLite.exe"])
    return shown


def test_an_unexpected_startup_error_shows_a_dialog_and_exits_2(windowed_env, monkeypatch):
    from camoufox_pm import cli

    def explode():
        raise RuntimeError("the backend refused to start")

    monkeypatch.setattr(cli, "main", explode)

    assert windowed.main() == 2
    assert any("could not start" in message for message in windowed_env)
    import os

    console = (Path(os.environ["CPM_DATA_DIR"]) / "logs" / "console.log").read_text(
        encoding="utf-8"
    )
    assert "the backend refused to start" in console, "the traceback is kept for support"


def test_a_nonzero_exit_without_its_own_message_still_gets_a_dialog(windowed_env, monkeypatch):
    """argparse writes to the log and exits; the window must not just vanish."""
    from camoufox_pm import cli

    def exit_badly():
        raise SystemExit(2)

    monkeypatch.setattr(cli, "main", exit_badly)

    assert windowed.main() == 2
    assert windowed_env, "an unexplained failure must still reach the user"


def test_a_normal_start_shows_no_dialog(windowed_env, monkeypatch):
    from camoufox_pm import cli

    monkeypatch.setattr(cli, "main", lambda: None)

    assert windowed.main() == 0
    assert windowed_env == []


def test_the_already_running_message_is_not_an_error_dialog(windowed_env, monkeypatch):
    from camoufox_pm import cli

    def already_running():
        portable.notify_fatal("Fingerprint Lite is already running (PID 1).")
        raise SystemExit(0)

    monkeypatch.setattr(cli, "main", already_running)

    assert windowed.main() == 0
    # notify_fatal reported it; no extra "could not start" dialog on top.
    assert len(windowed_env) == 1


def test_output_is_captured_into_the_data_logs_and_redacted(windowed_env, monkeypatch):
    from camoufox_pm import cli

    def shout():
        print("password=verysecretvalue")
        print("launched profile abc-123")

    monkeypatch.setattr(cli, "main", shout)

    assert windowed.main() == 0
    import os

    console = (Path(os.environ["CPM_DATA_DIR"]) / "logs" / "console.log").read_text(
        encoding="utf-8"
    )
    assert "launched profile abc-123" in console
    assert "verysecretvalue" not in console


def test_the_windowed_entry_forces_desktop_mode(windowed_env, monkeypatch):
    from camoufox_pm import cli

    seen = {}
    monkeypatch.setattr(cli, "main", lambda: seen.setdefault("argv", list(sys.argv)))

    assert windowed.main() == 0
    assert seen["argv"][1] == "--desktop", "double-click means the desktop window"
