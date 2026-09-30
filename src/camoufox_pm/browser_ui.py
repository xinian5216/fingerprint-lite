"""Human-facing browser defaults without changing a profile's device identity."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_SEARCH_ENGINE = "DuckDuckGo (Privacy)"
SEARCH_ENGINES = (
    ("DuckDuckGo (Privacy)", "https://duckduckgo.com/?q={searchTerms}", "@ddg"),
    ("Startpage", "https://www.startpage.com/sp/search?query={searchTerms}", "@sp"),
    ("Brave Search", "https://search.brave.com/search?q={searchTerms}", "@brave"),
    ("Google Search", "https://www.google.com/search?q={searchTerms}", "@google"),
    ("Bing Search", "https://www.bing.com/search?q={searchTerms}", "@bing"),
)


def firefox_prefs() -> dict[str, Any]:
    """Keep typed text local until the user submits a search.

    On Windows, let the OS draw the caption buttons. The pinned Camoufox build
    applies its content font allowlist to chrome too, hiding the icon font used
    by its custom titlebar. Native buttons need no extra web-visible fonts.
    """
    prefs: dict[str, Any] = {
        "browser.search.suggest.enabled": False,
        "browser.search.suggest.enabled.private": False,
        "browser.urlbar.suggest.searches": False,
    }
    if sys.platform == "win32":
        prefs["browser.tabs.inTitlebar"] = 0
    return prefs


def prepare_search_policy(install: Path) -> None:
    """Replace the pinned build's 'None' search policy, keeping other policies.

    Firefox's distribution policy adds engines and sets a default on policy
    changes; it preserves a user's subsequent default choice. We never write
    search.json.mozlz4/extensions.json or alter browser executables. The first
    original policies file is backed up beside it before atomic replace.
    The shared install lock prevents two managers racing the backup.
    """
    from camoufox_pm.browser_env import hold_install_lock

    path = Path(install) / "distribution" / "policies.json"
    with hold_install_lock(Path(install)):
        original = path.read_bytes()
        document = json.loads(original.decode("utf-8-sig"))
        policies = document["policies"]
        if not isinstance(policies, dict):
            raise ValueError("Browser policies must contain a policies object")
        search = {
            "PreventInstalls": False,
            # Policy engines cannot replace app-provided engines even when the
            # old policy hid them. Distinct names avoid those collisions; hide
            # the built-ins so existing profiles also get the same five choices.
            "Remove": [
                "None",
                "Google",
                "DuckDuckGo",
                "Bing",
                "Amazon.com",
                "eBay",
                "Twitter",
                "Wikipedia (en)",
            ],
            "Default": DEFAULT_SEARCH_ENGINE,
            "DefaultPrivate": DEFAULT_SEARCH_ENGINE,
            "Add": [
                {"Name": name, "URLTemplate": url, "Method": "GET", "Alias": alias}
                for name, url, alias in SEARCH_ENGINES
            ],
        }
        if policies.get("SearchEngines") == search:
            return
        policies["SearchEngines"] = search
        backup = path.with_name("policies.fingerprint-lite-original.json")
        if not backup.exists():
            with backup.open("xb") as handle:
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        logger.info("Browser search policy prepared: DuckDuckGo default, five engine choices")
