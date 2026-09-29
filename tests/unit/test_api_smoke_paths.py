"""Path resolution helpers for the standalone API smoke script."""

import sys
from importlib import import_module, reload
from pathlib import Path


class _StdoutSpy:
    def __init__(self) -> None:
        self.reconfigure_calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.reconfigure_calls.append(kwargs)


def test_import_does_not_reconfigure_stdout(monkeypatch) -> None:
    stdout = _StdoutSpy()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "argv", ["smoke/api_checks.py"])
    reload(import_module("smoke.api_checks"))

    assert stdout.reconfigure_calls == []


def test_profile_directory_uses_the_database_parent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["smoke/api_checks.py"])
    api_checks = import_module("smoke.api_checks")
    db_path = tmp_path / "isolated-data" / "profiles.db"
    expected = tmp_path / "isolated-data" / "profiles" / "profile_example"

    assert api_checks.profile_directory_for(db_path, "example") == expected
