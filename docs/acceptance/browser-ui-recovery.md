# Browser UI recovery (2026-09-30)

Continues `test/phase5a-acceptance` and Draft PR #8. No new PR, merge, tag,
release, browser-version change or profile migration.

The user can now open the manager and launch a browser, but reports an empty
search-engine switcher, a red mouse overlay, and missing-glyph caption buttons.
The screenshots were inspected directly. The fixed browser remains Camoufox
152.0.4-beta.30 with the same official archive SHA256.

## Causes and changes

- The pinned archive's `distribution/policies.json` removes common engines and
  defines `None` at `http://127.0.0.1`. Before a portable browser launch, replace
  only its SearchEngines section with five HTTPS policy engines: DuckDuckGo
  (initial normal/private default), Startpage, Brave Search, Google and Bing.
  Display names are `DuckDuckGo (Privacy)`, `Startpage`, `Brave Search`,
  `Google Search` and `Bing Search`. Names must differ from the built-in engines:
  Firefox cannot replace an app-provided engine through policy, even when hidden.
  Hide the old built-ins and None, retaining all non-search upstream policies.
  Default changes are applied by Firefox on policy modification; subsequent
  per-profile default choices must survive restart. Search suggestions stay off
  to avoid sending partially typed searches to a provider.
- The build's `browser-init.patch` creates `#cursor-highlighter` by default.
  Pass `config.showcursor = false` on every profile launch. This is chrome UI,
  not a machine identity, and no saved pin is rewritten.
- Its `font-hijacker.patch` applies a font whitelist globally, including browser
  chrome. Missing caption glyphs match the user's screenshot. On real Windows
  hosts set `browser.tabs.inTitlebar = 0` so Windows draws the normal system
  titlebar and caption controls. Keep saved/custom content fonts unchanged.

DuckDuckGo is a privacy-focused default, not a claim that a single engine is
objectively the most private. Its official policy says search/browsing history
is not saved or shared: https://duckduckgo.com/privacy . Engine choice remains
available through the browser's search UI/settings and @ddg/@sp/@brave/@google/@bing.

## Protection and rollback

The product now intentionally configures the pinned install's distribution
policy; its executable, packaged omni.ja, prefs/config files and archive pin are
unchanged. No direct writes to `search.json.mozlz4` or `extensions.json`.
An OS file lock serializes changes, a byte-for-byte original policy is saved as
`distribution/policies.fingerprint-lite-original.json`, and policy replacement
is atomic. An unchanged policy is not rewritten on later launches. A missing,
invalid or unwritable policy fails the launch without launching a partially
configured browser or replacing a malformed original.

Close browsers normally before updating program files. Retain `Data`, `Browser`,
`Temp`, `paths.env` and the `config.env` encryption key. Rollback: close browsers
and manager, restore the previous program build, then copy the policy backup to
`policies.json` in the same installed browser's distribution directory. Never
delete or reset an existing browser profile to repair its UI.

## Evidence / outstanding acceptance

- Local non-browser suite: 543 passed, 2 skipped, 25 deselected. Backend lint,
  formatting and mypy passed.
- Added real Windows check `scripts/check_browser_ui.py`: official verified
  pinned download, disposable profiles (including Unicode paths), real headed
  Camoufox/Playwright launch and test-only Marionette chrome inspection. Checks
  real search-service engine names/submission URLs, initial defaults, a user's
  Startpage choice after restart, a second profile's privacy default, absence of
  the highlighter, native Windows caption style bits, and unchanged saved pin.
- Windows execution and portable ZIP: **PENDING** until the workflow passes.
- True user-desktop visual appearance, search-switcher selection, submitted
  query navigation and minimise/maximise/restore/close clicks: **PENDING**.
  CI state checks do not constitute that visual acceptance.
- First-run handoff retains its existing OPEN stability gate; the user's single
  observed manager-open pass remains recorded in first-run-desktop-handoff.md.

Earlier search investigation was paused after an unsuccessful policy PoC. This
user request explicitly resumes the search feature. The supported distribution
policy is now tested against the actual pinned build before claiming it works;
profile-internal injection and browser binary changes remain outside this fix.
