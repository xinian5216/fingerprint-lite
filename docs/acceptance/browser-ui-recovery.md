# Browser UI recovery (2026-09-30)

Continues `test/phase5a-acceptance` and Draft PR #8. No new PR, merge, tag,
release, browser-major-version change or profile migration.

The user can now open the manager and launch a browser, but reports an empty
search-engine switcher, a red mouse overlay, and missing-glyph caption buttons.
The screenshots were inspected directly. The fixed browser remains Camoufox
152.0.4-beta.30 with the same official archive SHA256.

## Causes and changes

- The pinned archive's `distribution/policies.json` removes common engines and
  defines `None` at `http://127.0.0.1`. More fundamentally, the selector returns
  a v1 stub that Firefox 152's Rust search parser rejects: the actual Windows
  run reproduced `missing field recordType`, so policies alone cannot work.
  Backport the upstream v2 inert stub from `v152.0.4-beta.31` into a separate
  versioned runtime copy. No downloadable release exists for beta.31; the
  available new binaries also upgrade Firefox to 156 and change more identity
  behavior. Keep the verified beta.30 install intact and keep its executable,
  DLLs, fonts and fingerprint implementation bytes. Change only the selector
  module's known stub, remove bundled bytecode caches that could retain old JS,
  and verify every other archive resource before atomic directory promotion.
  Configure the copy's SearchEngines section with five HTTPS policy engines: DuckDuckGo
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

The product intentionally creates a beta.30 compatibility runtime next to the
verified original, named `*-fingerprint-lite-search-v2-1`. Its manifest records
source archive pin, original/patched omni.ja digests, original/patched module
digests and removed bytecode entries. A lock serializes creation, unrecognised
source is refused, staged directories are removed on failure, and a damaged
existing copy is refused rather than silently trusted or overwritten. Unchanged
files are hardlinked where possible; modified resources are replaced atomically
in the copy, leaving their original file inode untouched. Browser launch names
the copy's executable explicitly; global active-browser resolution stays pinned.
No direct writes to `search.json.mozlz4` or `extensions.json`.
Within the copy, a byte-for-byte original policy is saved as
`distribution/policies.fingerprint-lite-original.json`, and policy replacement
is atomic. An unchanged policy is not rewritten on later launches. A missing,
invalid or unwritable policy fails the launch without launching a partially
configured browser or replacing a malformed original.

Close browsers normally before updating program files. Retain `Data`, `Browser`,
`Temp`, `paths.env` and the `config.env` encryption key. Rollback: close browsers
and manager, restore the previous program build. It selects the retained official
install; the compatibility copy can stay on disk for diagnosis. Never
delete or reset an existing browser profile to repair its UI.

## Evidence / outstanding acceptance

- Local non-browser suite: 548 passed, 2 skipped, 25 deselected. Backend lint,
  formatting and mypy passed.
- Added real Windows check `scripts/check_browser_ui.py`: official verified
  pinned download, disposable profiles (including Unicode paths), real headed
  Camoufox/Playwright launch and test-only Marionette chrome inspection. Checks
  real search-service engine names/submission URLs, initial defaults, a user's
  Startpage choice after restart, a second profile's privacy default, absence of
  the highlighter, native Windows caption style bits, and unchanged saved pin.
- Windows real-browser check: **PASS** on application commit
  `9de3682fe1da6dc362f5e0dc6d2889344daca175`,
  [run 36742758250](https://github.com/xinian5216/fingerprint-lite/actions/runs/36742758250).
  It recorded exactly five visible engines and correctly encoded Unicode/query
  punctuation in HTTPS submission URLs. Fresh and second profiles defaulted to
  DuckDuckGo (Privacy); the first profile reopened with Startpage. All three
  windows reported no highlighter, native titlebar enabled, suggestions disabled,
  and real Windows caption/system-menu/minimise/maximise style bits present.
  Backend on Windows: 548 passed, 2 skipped; standard CI Python 3.10–3.13 and
  frontend also passed. This uses the real browser, not a mocked search service.
- [Portable test artifact](https://github.com/xinian5216/fingerprint-lite/actions/runs/36742758250/artifacts/11111157316):
  build and packaged console smoke **PASS**. The ZIP was downloaded and its hash,
  source commit, archive CRCs, fresh data boundary and embedded browser_ui,
  search_compat and geoip_compat modules were verified. ZIP SHA256:
  `145E9C17FE1852A71BBE17CDB1A1072256A6122F4028D288E6DA54C701E72067`.
  `browser-ui-check.json` in the artifact contains the actual engine/window
  evidence. The tested source omni.ja digest was
  `bc56050665ec1815c2af31d931a7d633c74805c97a4af9b4469c2554c1d1038b`;
  its selector module digest was
  `ca843d9379f8cf4b5ed04e3da35fa7ace2cbbe6f2ec5a652afea09f8642ffff3`.
- True user-desktop visual appearance, search-switcher selection, submitted
  query navigation and minimise/maximise/restore/close clicks: **PENDING**.
  CI state checks do not constitute that visual acceptance.
  Close all managed browsers and the manager before replacing program files.
  Then reopen an existing test profile, verify all five options in the search
  switcher, submit a harmless query, switch default in Settings → Search, restart,
  and verify the choice persists. Check the ordinary mouse and caption controls,
  including minimise, maximise/restore and normal close. Do not reset the profile.
- First-run handoff retains its existing OPEN stability gate; the user's single
  observed manager-open pass remains recorded in first-run-desktop-handoff.md.

Earlier search investigation was paused after an unsuccessful policy PoC. This
user request explicitly resumes the search feature. The supported distribution
policy is now tested against the actual pinned build before claiming it works;
profile-internal injection and browser executable/DLL/major-version changes
remain outside this fix. The resource backport is an explicit scoped change,
not a claim that the original upstream resource archive works unchanged.
