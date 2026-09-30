"""An isolated search backport must preserve the verified browser and profiles."""

import json
import zipfile

import pytest

from camoufox_pm import search_compat

MODULE = "moz-src/toolkit/components/search/SearchEngineSelector.sys.mjs"


@pytest.fixture
def install(tmp_path):
    folder = tmp_path / "下载" / "152.0.4-beta.30-test"
    folder.mkdir(parents=True)
    (folder / "camoufox.exe").write_bytes(b"MZ-unchanged-test-executable")
    (folder / "version.json").write_text('{"version":"152.0.4"}', encoding="utf-8")
    with zipfile.ZipFile(folder / "omni.ja", "w") as archive:
        archive.writestr(MODULE, "async function config() {\n" + search_compat._OLD_STUB + "\n}")
        archive.writestr("chrome/ui.svg", b"unchanged UI resource")
        archive.writestr("jsloader/selector.bin", b"obsolete selector bytecode")
    return folder


def test_copy_changes_only_search_stub_and_disposable_bytecode(install):
    source_archive = (install / "omni.ja").read_bytes()
    executable = (install / "camoufox.exe").read_bytes()
    runtime = search_compat.prepare_runtime(install)
    assert runtime != install
    assert (install / "omni.ja").read_bytes() == source_archive
    assert (runtime / "camoufox.exe").read_bytes() == executable
    with zipfile.ZipFile(runtime / "omni.ja") as archive:
        module = archive.read(MODULE).decode()
        assert "recordType" in module and "appliesTo" not in module
        assert archive.read("chrome/ui.svg") == b"unchanged UI resource"
        assert "jsloader/selector.bin" not in archive.namelist()
    manifest = json.loads((runtime / "fingerprint-lite-search-compat.json").read_text())
    assert manifest["original_archive_sha256"] == search_compat._sha256(install / "omni.ja")
    assert manifest["patched_archive_sha256"] == search_compat._sha256(runtime / "omni.ja")
    assert manifest["removed_bytecode_caches"] == ["jsloader/selector.bin"]


def test_reuses_a_verified_copy_without_rebuilding(install, monkeypatch):
    first = search_compat.prepare_runtime(install)

    def refuse(*args, **kwargs):
        raise AssertionError("must reuse")

    monkeypatch.setattr(search_compat, "_patch_archive", refuse)
    assert search_compat.prepare_runtime(install) == first


def test_refuses_corrupted_compatibility_copy_keeps_original(install):
    runtime = search_compat.prepare_runtime(install)
    source = (install / "omni.ja").read_bytes()
    (runtime / "omni.ja").write_bytes(b"corrupt")
    with pytest.raises(search_compat.SearchCompatibilityError, match="validation"):
        search_compat.prepare_runtime(install)
    assert (install / "omni.ja").read_bytes() == source


def test_unknown_stub_is_rejected_without_promoting_copy(install):
    with zipfile.ZipFile(install / "omni.ja", "w") as archive:
        archive.writestr(MODULE, "unknown or newer source")
    original = (install / "omni.ja").read_bytes()
    with pytest.raises(search_compat.SearchCompatibilityError, match="known beta.30"):
        search_compat.prepare_runtime(install)
    assert (install / "omni.ja").read_bytes() == original
    assert not list(install.parent.glob("*fingerprint-lite*"))


def test_failed_promotion_keeps_original_and_removes_staging(install, monkeypatch):
    original = (install / "omni.ja").read_bytes()

    def refuse(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(search_compat.os, "replace", refuse)
    with pytest.raises(PermissionError):
        search_compat.prepare_runtime(install)
    assert (install / "omni.ja").read_bytes() == original
    assert not list(install.parent.glob("*fingerprint-lite*"))
