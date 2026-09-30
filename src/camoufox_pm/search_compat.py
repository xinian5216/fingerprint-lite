"""Backport the upstream v2 search stub into an isolated beta.30 runtime copy.

The official, SHA256-verified install is retained untouched. Only the broken
SearchEngineSelector stub and bundled startup bytecode caches differ in this
versioned copy; executable/DLL/font/fingerprint implementation bytes are kept.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from copy import copy
from pathlib import Path

from loguru import logger

REVISION = "search-v2-1"
_MODULE_SUFFIX = "toolkit/components/search/SearchEngineSelector.sys.mjs"
# Exactly the stub shipped in the pinned beta.30 source. Refuse another shape.
_OLD_STUB = """      return [
        {
          "appliesTo": [{
            "default": "yes",
            "included": {
              "everywhere": true
            },
            "webExtension": {
              "id": "none@mozilla.org"
            }
          }],
        },
      ];"""
# Same inert v2 configuration as upstream's no-search-engines.patch at
# v152.0.4-beta.31 (no downloadable release was published for that tag).
_V2_RECORDS = [
    {
        "recordType": "engine",
        "identifier": "none",
        "base": {
            "name": "None",
            "classification": "unknown",
            "urls": {
                "search": {
                    "base": "http://127.0.0.1/",
                    "method": "GET",
                    "searchTermParamName": "q",
                }
            },
        },
        "variants": [{"environment": {"allRegionsAndLocales": True}}],
    },
    {"recordType": "defaultEngines", "globalDefault": "none", "specificDefaults": []},
    {"recordType": "engineOrders", "orders": []},
]


class SearchCompatibilityError(RuntimeError):
    """A known, isolated compatibility runtime could not be prepared."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_or_link(source: str, destination: str) -> str:
    # A later atomic replacement in the copy cannot modify the original hardlink.
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def _find_module(install: Path) -> tuple[Path, str]:
    matches: list[tuple[Path, str]] = []
    for relative in (Path("omni.ja"), Path("browser/omni.ja")):
        archive = install / relative
        if archive.is_file():
            with zipfile.ZipFile(archive) as source:
                matches.extend(
                    (relative, name) for name in source.namelist() if name.endswith(_MODULE_SUFFIX)
                )
    if len(matches) != 1:
        raise SearchCompatibilityError(
            "Pinned browser must contain exactly one search selector module"
        )
    return matches[0]


def _patch_archive(source_path: Path, destination: Path, module: str) -> dict[str, object]:
    with zipfile.ZipFile(source_path) as source:
        original = source.read(module)
        text = original.decode("utf-8")
        if text.count(_OLD_STUB) != 1:
            raise SearchCompatibilityError("Pinned search selector is not the known beta.30 stub")
        replacement = "      return " + json.dumps(_V2_RECORDS, separators=(",", ":")) + ";"
        patched = text.replace(_OLD_STUB, replacement).encode("utf-8")
        removed_caches: list[str] = []
        with zipfile.ZipFile(destination, "w") as target:
            target.comment = source.comment
            for entry in source.infolist():
                # Embedded bytecode can still contain the old selector. Let
                # Firefox regenerate these disposable caches for the new copy.
                if entry.filename.startswith(("jsloader/", "startupCache/")):
                    removed_caches.append(entry.filename)
                    continue
                data = patched if entry.filename == module else source.read(entry)
                target.writestr(copy(entry), data)
        with zipfile.ZipFile(destination) as result:
            if result.testzip() is not None or result.read(module) != patched:
                raise SearchCompatibilityError("Compatibility archive failed verification")
            # Every non-cache resource other than this module must be identical.
            if set(result.namelist()) != set(source.namelist()) - set(removed_caches):
                raise SearchCompatibilityError("Compatibility archive changed resource names")
            for entry in result.infolist():
                if entry.filename != module:
                    if result.read(entry) != source.read(entry.filename):
                        raise SearchCompatibilityError(
                            "Compatibility archive changed another resource"
                        )
    return {
        "module": module,
        "original_module_sha256": hashlib.sha256(original).hexdigest(),
        "patched_module_sha256": hashlib.sha256(patched).hexdigest(),
        "removed_bytecode_caches": removed_caches,
        "original_archive_sha256": _sha256(source_path),
        "patched_archive_sha256": _sha256(destination),
    }


def prepare_runtime(install: Path) -> Path:
    """Build once, verify before promotion, and reuse without editing the source."""
    from camoufox_pm.browser_env import PINNED_SHA256, hold_install_lock

    install = Path(install)
    variant = install.with_name(f"{install.name}-fingerprint-lite-{REVISION}")
    with hold_install_lock(install):
        manifest_path = variant / "fingerprint-lite-search-compat.json"
        if variant.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                resource = manifest["archive"]
                if (
                    manifest["revision"] != REVISION
                    or manifest["source_archive_pin"] != PINNED_SHA256
                    or _sha256(install / resource) != manifest["original_archive_sha256"]
                    or _sha256(variant / resource) != manifest["patched_archive_sha256"]
                ):
                    raise ValueError("Compatibility runtime digest mismatch")
            except (OSError, ValueError, KeyError) as exc:
                raise SearchCompatibilityError(
                    "Browser search compatibility copy failed validation; original install is intact"
                ) from exc
            return variant
        relative, module = _find_module(install)
        staging = Path(tempfile.mkdtemp(prefix=".fingerprint-lite-search-", dir=install.parent))
        try:
            shutil.copytree(
                install,
                staging,
                dirs_exist_ok=True,
                copy_function=_copy_or_link,
                ignore=shutil.ignore_patterns(".install.lock"),
            )
            patched = staging / "search-compat-omni.tmp"
            record = _patch_archive(install / relative, patched, module)
            os.replace(patched, staging / relative)
            manifest = {
                "revision": REVISION,
                "source_archive_pin": PINNED_SHA256,
                "archive": relative.as_posix(),
                **record,
            }
            (staging / manifest_path.name).write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            staging.rename(variant)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        logger.info("Prepared isolated beta.30 search configuration compatibility runtime")
        return variant
