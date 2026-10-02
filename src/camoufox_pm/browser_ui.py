"""Human-facing browser UI fixes in an isolated copy of the installed engine.

Camoufox 152 beta.30 ships a v1 search stub that its v2 selector cannot read,
and a policy that removes search engines. A preference alone cannot repair
either. Keep the verified upstream install untouched and atomically prepare
a product-owned runtime with a valid stub, search policy and vector buttons.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zipfile import ZipFile

from . import browser_env

_SELECTOR = "moz-src/toolkit/components/search/SearchEngineSelector.sys.mjs"
_REVISION = "1"

SEARCH_ENGINES = (
    ("Startpage", "https://www.startpage.com/sp/search?query={searchTerms}", "@sp"),
    ("DuckDuckGo", "https://duckduckgo.com/?q={searchTerms}", "@ddg"),
    ("Brave Search", "https://search.brave.com/search?q={searchTerms}", "@brave"),
    ("Google", "https://www.google.com/search?q={searchTerms}", "@google"),
    ("Bing", "https://www.bing.com/search?q={searchTerms}", "@bing"),
)

# The same inert v2 configuration used by upstream's fix for camoufox#737.
# Real providers are installed by policy, without fetching Remote Settings.
_SEARCH_CONFIG = [
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


def _repair_selector(source: str) -> str:
    start = source.index("  async #getConfiguration(firstTime = true) {")
    end = source.index("    let result = [];", start)
    stub = source[start:end]
    if '"recordType"' in stub:
        return source
    if '"none@mozilla.org"' not in stub or '"appliesTo"' not in stub:
        raise ValueError("Unsupported Camoufox search configuration; runtime was not changed")
    replacement = (
        "  async #getConfiguration(firstTime = true) {\n"
        "    // Fingerprint Lite: valid offline search configuration.\n"
        f"    if (true) {{ return {json.dumps(_SEARCH_CONFIG)}; }}\n"
    )
    return source[:start] + replacement + source[end:]


def _search_policy(document: dict[str, Any]) -> dict[str, Any]:
    policies = document["policies"]
    search = policies.setdefault("SearchEngines", {})
    names = {name for name, _, _ in SEARCH_ENGINES}
    search["Remove"] = sorted((set(search.get("Remove", [])) - names) | {"None"})
    search["PreventInstalls"] = False
    search["Default"] = "Startpage"
    # Keep unrelated enterprise policies, including telemetry and update bans.
    search["Add"] = [
        entry for entry in search.get("Add", []) if entry["Name"] not in names | {"None"}
    ] + [
        {"Name": name, "URLTemplate": url, "Method": "GET", "Alias": alias}
        for name, url, alias in SEARCH_ENGINES
    ]
    policies["SearchSuggestEnabled"] = False
    return document


def _window_controls_css() -> str:
    # Masks inherit the button colour in dark/light/high-contrast modes. Keep
    # Firefox's native button handlers, hit areas and snap-layout appearance.
    paths = {
        "min": "M1 5.5h8",
        "max": "M1.5 1.5h7v7h-7z",
        "restore": "M3.5 1.5h5v5M1.5 3.5h5v5h-5z",
        "close": "M1.5 1.5l7 7m0-7l-7 7",
    }
    rules = [
        "/* Fingerprint Lite: font-independent Windows window controls. */",
        "@media (-moz-platform: windows) {",
        ".titlebar-button::before { content: '' !important; width: 10px; height: 10px; "
        "background-color: currentColor; mask-size: 10px 10px; "
        "mask-repeat: no-repeat; mask-position: center; }",
    ]
    for name, path in paths.items():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            f'<path d="{path}" fill="none" stroke="black"/></svg>'
        )
        rules.append(
            f'.titlebar-{name}::before {{ mask-image: url("data:image/svg+xml,{quote(svg)}"); }}'
        )
    return "\n".join(rules) + "\n}\n"


def _patch_runtime(root: Path) -> None:
    archive = root / "omni.ja"
    replacement = root / "omni.ja.new"
    with ZipFile(archive) as original, ZipFile(replacement, "w") as updated:
        if _SELECTOR not in original.namelist():
            raise ValueError("Camoufox search module was not found")
        for entry in original.infolist():
            data = original.read(entry)
            if entry.filename == _SELECTOR:
                data = _repair_selector(data.decode("utf-8")).encode("utf-8")
            updated.writestr(entry, data)
    replacement.replace(archive)
    policy = root / "distribution" / "policies.json"
    document = _search_policy(json.loads(policy.read_text(encoding="utf-8")))
    policy.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    css = root / "chrome.css"
    with css.open("a", encoding="utf-8") as handle:
        handle.write("\n" + _window_controls_css())


def prepare_runtime(executable: Path, cache_dir: Path) -> Path:
    """Return a reusable runtime, publishing only after every repair succeeds.

    The cache key includes the source path/build and UI revision, so different
    installs never reuse each other's runtime. Separate staging directories
    allow simultaneous launches from multiple processes without partial copies.
    """
    executable = executable.resolve()
    if not executable.is_file():
        raise ValueError(f"Camoufox executable was not found: {executable}")
    root = executable.parent
    if executable.name == "camoufox" and root.name == "MacOS":
        root = root.parent / "Resources"
    tree = root.parent if root.name == "Resources" else root
    cache_dir = cache_dir.resolve()
    if cache_dir.is_relative_to(tree):
        raise ValueError("The UI runtime cache must be outside the source browser installation")
    relative_root = root.relative_to(tree)
    relative_executable = executable.relative_to(tree)
    metadata = (root / "version.json").read_bytes()
    identity = hashlib.sha256(str(tree).encode() + metadata + _REVISION.encode()).hexdigest()[:20]
    target = cache_dir / identity
    ready = target / ".fingerprint-lite-ui-ready"
    if ready.is_file() and (target / relative_executable).is_file():
        return target / relative_executable
    cache_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ui-", dir=cache_dir))
    try:
        shutil.copytree(tree, stage, dirs_exist_ok=True)
        _patch_runtime(stage / relative_root)
        (stage / ready.name).write_text(_REVISION, encoding="ascii")
        try:
            stage.rename(target)
        except OSError:
            if not ready.is_file():
                raise
        return target / relative_executable
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def prepare_launch_options(options: dict[str, Any]) -> dict[str, Any]:
    """Apply UI fixes on every managed launch, including existing profiles."""
    from camoufox.pkgman import launch_path

    from .geoip_compat import install_windows_geoip_reader

    install_windows_geoip_reader()

    launch = dict(options)
    if launch.get("browser") and not launch.get("executable_path"):
        from camoufox.multiversion import find_installed_version

        source = find_installed_version(launch["browser"])
        if source is None:
            raise ValueError(f"Browser version {launch['browser']!r} is not installed")
        executable = Path(launch_path(source))
    else:
        executable = Path(launch.get("executable_path") or launch_path())
    launch["executable_path"] = str(
        prepare_runtime(executable, browser_env.default_offline_dir() / "ui-runtime")
    )
    launch["config"] = {**launch.get("config", {}), "showcursor": False}
    return launch
