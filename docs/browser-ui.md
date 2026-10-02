# Browser search and window controls

New profiles use **Startpage** initially. The browser search menu also offers
DuckDuckGo, Brave Search, Google and Bing. Use an engine for one search from the
menu, or change the default in the browser's **Settings → Search** page. A
default you choose is kept for that profile when you close and reopen it.
The aliases `@sp`, `@ddg`, `@brave`, `@google` and `@bing` also select an engine.

Startpage is a privacy-focused default, not a claim that any engine can guarantee
complete anonymity. Its [privacy policy](https://www.startpage.com/en/privacy-policy/)
describes how it handles searches. Remote suggestions are disabled by default,
so typing in the address bar does not send partial queries to these engines.

Browser windows use the computer's normal mouse cursor without Camoufox's red
highlighter. Windows minimize, maximize, restore and close buttons use vector
icons, which remain readable when a profile restricts available fonts. Their
native click behavior, hover colors and hit areas are preserved.

## Existing profiles and installation

The fixes apply the next time a profile is launched, including profiles created
before this update. An existing profile with no working search default receives
Startpage. Later changes to the default are not overwritten on each launch.
Cookies, logins, storage and the pinned hardware fingerprint are preserved.

The pinned Camoufox `152.0.4-beta.30` build has a malformed search configuration
and an enterprise policy that removes search providers. Fingerprint Lite makes
an independent copy under `Browser/ui-runtime/` (or the selected Browser path),
repairs the search module and policy there, and adds the window-control CSS.
It keeps all unrelated enterprise policies, including telemetry and update
restrictions. The original verified install is left unchanged.

Preparing this copy requires room for one additional extracted browser. It runs
once per source build and UI revision; subsequent launches reuse the completed
runtime. Failed preparation never publishes a partial runtime, and concurrent
launches cannot see a half-written copy.

## GeoIP databases in Windows paths

An installed GeoIP database can appear to be missing when its Windows directory
contains Chinese or other non-ASCII characters. MaxMind's optional native reader
uses a filename API that rejects those paths; Python can still see and read the
same file. Fingerprint Lite selects MaxMind's supported Python mmap reader for
automatic Unicode-path reads in this process. ASCII paths, other platforms and
explicit reader modes keep their normal behavior. No upstream package files,
database contents or profile data are rewritten.

This covers browser startup, browser environment preparation and proxy checks.
The proxy check now distinguishes an unreadable database from a missing or
damaged one and from an address absent from the database. Its messages follow
the UI's selected Chinese or English language. A successful network connection
does not imply the database can determine the exit's country and timezone.

The offline launch regression uses a licensed synthetic MMDB in a Chinese
directory and checks the timezone resolved by a script in the real page:

```bash
uv run pytest tests/unit/test_geoip_compat.py
uv run pytest tests/browser/test_unicode_geoip_launch.py --no-network
```

## Browser UI validation

Run the real browser check after installing Camoufox:

```bash
uv run pytest tests/browser/test_browser_ui.py --no-network
```

It reads the actual browser search service and window styles, checks Unicode
query encoding without visiting the search providers, and verifies that changing
the default survives a restart. Marionette is enabled only for the isolated
test process, never for production launches.
