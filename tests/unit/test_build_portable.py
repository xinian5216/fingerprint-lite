"""The portable assembly refuses to package a stale or half-built bundle.

A release-shaped artifact that is missing its double-click entry or its web UI
is worse than no artifact: it looks finished. These tests drive the assembly
against a synthetic bundle, so they run without a PyInstaller build.
"""

import importlib.util
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_portable", Path(__file__).resolve().parents[2] / "scripts" / "build_portable.py"
)
build_portable = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_portable)


def fake_bundle(tmp_path: Path, *, with_gui: bool = True, with_webui: bool = True) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "_internal" / "camoufox_pm" / "webui").mkdir(parents=True)
    (bundle / "camoufox-pm.exe").write_bytes(b"console entry")
    if with_gui:
        (bundle / "FingerprintLite.exe").write_bytes(b"windowed entry")
    if with_webui:
        (bundle / "_internal" / "camoufox_pm" / "webui" / "index.html").write_text("<html>")
    return bundle


def test_a_bundle_without_the_double_click_entry_is_refused(tmp_path):
    bundle = fake_bundle(tmp_path, with_gui=False)
    with pytest.raises(SystemExit) as excinfo:
        build_portable.assemble(bundle, tmp_path / "out", "0.1.0-alpha.1", "152.0.4-beta.30")
    assert "FingerprintLite.exe" in str(excinfo.value)


def test_a_bundle_without_the_web_ui_is_refused(tmp_path):
    bundle = fake_bundle(tmp_path, with_webui=False)
    with pytest.raises(SystemExit) as excinfo:
        build_portable.assemble(bundle, tmp_path / "out", "0.1.0-alpha.1", "152.0.4-beta.30")
    assert "webui/index.html" in str(excinfo.value)


def test_assemble_adds_the_portable_bits(tmp_path):
    bundle = fake_bundle(tmp_path)
    target = build_portable.assemble(bundle, tmp_path / "out", "0.1.0-alpha.1", "152.0.4-beta.30")

    assert target.name == "FingerprintLite-0.1.0-alpha.1-windows-x64"
    assert (target / "FingerprintLite.exe").exists()
    assert (target / "camoufox-pm.exe").exists()
    assert (target / "Data" / "README.txt").exists()
    assert (target / "Browser" / "README.txt").exists()
    assert (target / "LICENSE").read_bytes() == (build_portable.ROOT / "LICENSE").read_bytes()
    notices = (target / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert "MPL-2.0" in notices
    assert "Camoufox Profile Manager Contributors" in " ".join(notices.split())
    readme = (target / "README-portable.txt").read_text(encoding="utf-8")
    assert "152.0.4-beta.30" in readme, "the README names the pinned browser build"
    assert "Data" in readme and "Browser" in readme


def test_the_zip_holds_the_folder_under_its_own_name(tmp_path):
    bundle = fake_bundle(tmp_path)
    target = build_portable.assemble(bundle, tmp_path / "out", "0.1.0-alpha.1", "152.0.4-beta.30")
    archive = build_portable.zip_folder(target)

    assert archive.name == "FingerprintLite-0.1.0-alpha.1-windows-x64.zip"
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "FingerprintLite-0.1.0-alpha.1-windows-x64/FingerprintLite.exe" in names
    assert "FingerprintLite-0.1.0-alpha.1-windows-x64/Data/README.txt" in names
