# Phase 5A acceptance (short regression + handoff)

- Date: 2026-09-30
- Baseline: public `main @ dfd62a1`, plus unmerged local fix `41757b1`
  (concurrent-install guard — acceptance branch carries it for validation;
  see below). No version/tag/release changes.
- Environment: Windows 10 x64, Python 3.12, Node 22, local scratch dirs on
  D: (untracked `.work/`), loopback-only test proxy. No real accounts;
  all proxy credentials synthetic.
- Scope: acceptance only. No new features were built in this round.

## Results

| # | Item | Result |
|---|---|---|
| 1 | First-start wizard (zh default, Browse buttons, lang switch, core flow) | PASS (lang switch PARTIAL, see notes) |
| 2 | Browser-install progress stages (offline ZIP path, no re-download) | PASS |
| 3 | Web bilingual UI (switch, pages, persistence) | PASS |
| 4 | Proxy locale (auto / manual-preserved / dead-proxy) | PASS |
| 5 | Basic stability (launch, close, ports, child processes) | PASS |

### 1. Wizard — PASS (switch PARTIAL)

- Fresh wizard renders Simplified Chinese by default (window title, heading,
  all field labels/hints, engine options, buttons).
- A Browse click opens the native folder picker (observed live).
- Core flow verified end-to-end twice: custom Data/Browser/Temp written to
  `paths.env`, official offline ZIP installed with matching SHA256 pin,
  GeoIP prepared, window closes on success.
- PARTIAL: the English radio was not visually confirmed (automation clicks
  missed the small control on a shared desktop). The dictionary render is
  unit-tested and the identical switch mechanism is verified live in the
  web UI (item 3).

### 2. Progress stages — PASS

- Offline official-ZIP install drives the full stage pipeline:
  downloading (byte counts) → verifying → extracting → preparing → done,
  with success closing the wizard and failure rendering the error in place.
- MB/percent reporting is covered by unit tests with event assertions; an
  earlier online run was observed filling to 100%.

### 3. Web UI — PASS

- Headless run against the source server: default nav renders Chinese;
  EN switch flips nav + `<html lang>`; reload keeps English via
  localStorage; switching back restores Chinese.
- Profiles list, New Profile modal and Settings pages render with no
  errors and no broken layout (screenshots on file, kept out of git).

### 4. Proxy locale — PASS

- Live check through the loopback test proxy (CN exit): default-language
  profile received Chinese languages + locale.
- Hand-set language profile: untouched (locale stays empty).
- Unreachable proxy: settings unchanged.
- The explicit in-form fill button follows the same table.

### 5. Stability — PASS

- Headless launch ran (browser processes under the custom Data dir),
  API close returned `closed`, active list empty, no remaining
  browser/WebView2/Node child processes, test ports released.

## Issues

None filed. One pre-existing acceptance hazard was already fixed on this
branch: concurrent installs into one Browser folder could fail obscurely
mid-verification (commit `41757b1`: atomic Start guard + per-root OS file
lock + worker traceback logging; 47 unit tests pass). It awaits its own PR;
this acceptance branch only carries it for validation.

## Not covered / still paused

- Phase 5B global search engine: PAUSED per stop condition (empty engine
  list proof; see `docs/phase5b-handoff.md`). No code written for it.
- WebView2-missing and Windows 11 checks remain untested (unchanged).
- No tag, release, PyPI, merge, or version bump in this round.

## Resume point

This branch (`test/phase5a-acceptance`) is the recovery entry: acceptance
evidence lives in ignored `.work/` beside it. Next: real-usage regression
of Phase 5A, then decide about alpha.2 — unhurried.
