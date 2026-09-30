# Phase 5B handoff — global search-engine PoC (IN PROGRESS, paused overnight)
Date: 2026-09-29 EOD. Phase 5B is NOT complete; this is a safe-parking record.

## Repo state

- Branch: `main`; HEAD: `41757b1` (fix: serialize concurrent browser installs).
- `origin/main` is `dfd62a1`; local main is ahead by 1 commit, **not pushed**.
- Worktree: clean. No stash. No tag/release/version changes today.

## Goal (unchanged)

Validate an install-level global search engine (`distribution/policies.json`
+ `SearchEngines` Add/Default) on the pinned Camoufox 152 build, instead of
the paused per-profile extension route.

## What was proven today

- `policies.json` next to the installed build does NOT break launch: multiple
  headed launches rendered and browsed normally (example.com loads+screenshots).
- The `addons` launch channel is dead for persistent profiles (5A finding,
  re-confirmed): `launch_options()` drops addons silently; the launched
  profile's `extensions.json` had only built-ins (not even default uBO).
- Playwright-Juggler cannot render privileged `about:` pages here
  (`about:preferences`, `about:newtab` blank, `about:policies` hangs the tab);
  normal sites are unaffected.
- OS-level driving works (window place/click/clipboard-paste verified by
  screenshot), but this shared desktop is actively used and focus fights it.

## What is NOT proven (the remaining gap)

- Whether the policy actually registers Startpage and makes it default
  (Settings → Search row, address-bar search landing on startpage.com,
  all-profiles consistency, removal restores Google default).
- `search.json.mozlz4` is never written by these automated runs (search
  service never initializes without real UI use), so the store cannot be
  used as a shortcut proof.

## The EINVAL incident (open, prime suspect identified)

- Real wizard run (custom D: dirs, correct `paths.env` written) downloaded to
  ~100%, then failed at verify stage with bare `[Errno 22] Invalid argument`.
- The same download→verify→extract path passes 3/3 in scripted reproductions
  (relative paths, absolute custom paths, pywebview-process variant).
- Prime suspect: two concurrent installs into one Browser folder (a second
  Start, or two wizard windows — duplicate wizard processes were observed
  that day). Committed guard fix `41757b1` (atomic Start guard + per-root OS
  file lock + worker traceback logging; 47 unit tests pass, ruff/mypy clean)
  but it is **not yet validated against the real failure** — nobody has
  clicked Start since the fix.

## Local-only items (all under ignored `.work/`, never committed)

- PoC scripts/logs/screenshots: `.work/policy_probe*.py/.log`, `.work/*pp*.png`,
  `.work/hold_browser.py`, `.work/holdprofile*`, `.work/os_*.ps1`,
  `.work/repro_*.py/.log`, `.work/search-store-check/`, `.work/accept5b/`,
  `.work/wizaccel/`, `.work/wizrepro*/`.
- Test `policies.json` was REMOVED from the dev browser install; the install
  itself (`152.0.4-beta.30-ea52a02f/camoufox.exe`) and all test Profile data
  were left intact. Test servers/proxies were stopped; ports free.

## Tomorrow: first steps in order

1. Re-verify the wizard window state; with fields already correct, click Start
   once and watch: if it completes, the guard fix is validated — record it.
2. If it fails again WITH the new traceback log, paste the traceback here and
   debug from evidence instead of guessing.
3. Only then resume the policy proof: relaunch with policy, place window
   topmost, click the address-bar engine dropdown, screenshot the engine list
   (Startpage present + default mark = proof, one click, no typing).
4. STOP CONDITION: if the working route requires writing Firefox profile
   internals (`search.json.mozlz4`, `extensions.json`) or touching the
   Camoufox engine, terminate the search-engine line entirely.

## Known risks / pending

- Shared desktop focus fights OS-level driving; prefer quiet hours for it.
- `origin/main` still `dfd62a1`; `41757b1` + this doc are local-only until
  deliberately pushed (via PR, not direct, when 5B resumes).
- No version/tag/release actions taken or pending.

## Outcome 2026-09-30 — search-engine line PAUSED (stop condition hit)

- Engine-list proof FAILED: with the policy in place, the address-bar engine
  popup ("This time search with:") is EMPTY — Startpage is neither listed nor
  default (user screenshot on file: `.work/` evidence).
- Supporting signal: Camoufox itself unregisters bundled engines
  (`browser.policies.runOncePerModification.extensionsUninstall` lists
  google/bing/amazon/ebay/twitter), so a policy `Add` lands in a stack that
  is already fighting engine registration — not a stable base for a default.
- Per the task stop rule the feature is PAUSED, not implemented. No product
  code was written for it; nothing to revert. Do NOT pursue profile-internal
  injection (`search.json.mozlz4`, `extensions.json`) or engine changes.
- Test `policies.json` removed from the dev browser install; launches,
  installs, caches and Profile data left intact.
- EINVAL follow-up CLOSED: the custom-dirs wizard run completed end-to-end
  with the guard fix in place (download → verify → extract → GeoIP → done,
  `version.json` present). The 47-test guard commit stands validated.
- Suggested next: Phase 5A real-usage regression first; then decide about
  alpha.2. Do not rush either.
