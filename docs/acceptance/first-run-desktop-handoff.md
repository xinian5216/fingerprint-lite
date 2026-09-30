# First-run → desktop handoff (open issue, WIP-era record)

- Date: 2026-09-30 (near EOD stop)
- Branch: `test/phase5a-acceptance` (Draft-only recovery branch; PR #8, DO NOT
  merge). Baseline: `origin/main @ dfd62a1` plus earlier branch commits
  (`41757b1` install-race guard, 5B handoff docs).
- Status of this fix: **PARTIAL / WIP**. Code is in the tree and lint-clean;
  it is NOT validated on a real first launch yet.

## User observation (the bug)

First run of `FingerprintLite.exe`: wizard completes browser download,
SHA256 verify, extraction and GeoIP; the wizard window closes by itself; the
Fingerprint Lite **management window never appears**.

## Confirmed code path (source, not speculation)

`FingerprintLite.exe` (windowed entry) → `windowed.main()` → `cli.main()` →
`_maybe_run_first_start_wizard()` → `wizard.run_wizard()` → `_open_window()`
→ `webview.start()` (first GUI loop) → on success `window.destroy()` →
control returns to `cli.main()` → `portable.bootstrap()` → `run_desktop()` →
`webview.start()` (**second GUI loop in the same process**).

## What has actually been verified (facts only)

- A plain Python process can run `webview.start()` twice in a row — the loop
  itself does not raise (checked locally with a two-window probe).
- A reproduced run from source (wizard-frozen path: `webview.start()` →
  background server starts, UI pages served and polled) ended with the
  process alive, **zero windows**, and no port listening, i.e. the second
  loop never yields a window — that matches the user report and the
  "second webview.start() never produces a window" theory, but it was
  observed through the scripted wizard path, not the packaged EXE.
- With a completed first-run script (offline ZIP + custom dirs) the wizard
  process reached GeoIP and exited cleanly; `paths.env`, browser install and
  `version.json` were all written — i.e. the setup side completes and the
  handoff after it is where the window is lost.

## What is NOT verified

- Real `FingerprintLite.exe` first launch after this change (not done — no
  time; it needs a fresh unzip, no `paths.env`, full first-run through the
  packaged windowed entry).
- Whether the relaunched process is ever blocked by the portable instance
  lock (see current changes).

## Current changes (uncommitted at stop time; target own PR later)

- `src/camoufox_pm/wizard.py`: `WizardResult.paths_written` records that this
  run created `paths.env`; a worker that just completed a first-run sets
  `state["relaunch"]` (windowed only) so the bridge reports it.
- `src/camoufox_pm/portable.py`: new `is_windowed()`; new
  `resolve_program_exe(program_dir)` returning the packaged
  `FingerprintLite.exe` beside the program root (else `None`).
- `src/camoufox_pm/cli.py`: `_maybe_run_first_start_wizard()` calls
  `_relaunch_for_manager()` when `result.paths_written and
  portable.is_windowed()`; that spawns `FingerprintLite.exe --desktop`
  (other argv preserved except `--desktop`), verifies `paths.env` exists
  before spawning, and ends this process with exit code 0. Source runs
  (no EXE) continue in-process exactly as before.

Intent: one GUI loop per process. The wizard process becomes a launcher; the
new process sees `paths.env`, skips the wizard and enters `run_desktop()`.

## Tests

- `tests/unit/test_cli.py`: added six first-run handoff tests (relaunch once
  with the exact command; relaunched start skips wizard and reaches the
  desktop path; cancel never relaunches; wizard failure exits 2 and never
  spawns; existing `paths.env` start unchanged; console/source run never
  relaunches).
- Current state at stop: `ruff check` + `ruff format` pass; the six new tests
  were being debugged one-by-one (progress: 5/6 selected pass; the primary
  test needed `should_show_wizard` pinned and was still not green when work
  stopped). `mypy` was NOT run after the final edits.

### Known test-harness caveats (for tomorrow)

- `tests/unit/test_cli.py` has pre-existing failures in this environment
  for the user-management tests (`sqlite3.IntegrityError: UNIQUE constraint
  failed: users.username`) and
  `test_the_portable_flag_is_accepted_in_source_runs` — these fail on the
  baseline commit too (verified with `git stash`), so they are not caused by
  this change.
- The `run` fixture keeps `portable._windowed` in whatever state earlier
  tests leave it; tests that need the windowed branch must pin
  `portable.is_windowed` (now done in the new tests).

## Tomorrow — first steps

1. In the new checkout: `gh pr checkout 8`.
2. Confirm the pre-existing baseline failures still fail without my diff:
   `git stash push -- src tests` then run `pytest tests/unit/test_cli.py -q`
   and compare; `git stash pop`.
3. Run only the first-run tests:
   `pytest tests/unit/test_cli.py -q -k "relaunch or first_run or cancelling_the_wizard or existing_paths_env"`.
4. Finish the primary test by also pinning `should_show_wizard` → True and
   asserting `spawned == [["…\\FingerprintLite.exe", "--desktop"]]`, then run
   `mypy` and the full `-m "not browser"` suite.
5. Real-machine test (the only real proof): fresh unzip of the portable ZIP,
   ensure no `paths.env`, start `FingerprintLite.exe`, walk the wizard once,
   confirm the manager window appears within seconds, then close it and
   confirm the port is released and no child process remains.
6. If the manager still does not appear, capture
   `Data/logs/console.log` from the run and the process list — do not guess.

## Not done / deliberately out of scope

- No change to `desktop.py`'s GUI loop, no pywebview architecture change,
  no Camoufox changes, no pin change, no version/tag/release.
- Real-machine validation of this fix is pending.

## `.work/` files worth carrying to the new machine (nothing here contains
secrets; all are local-only scratch)

- `.work/repro_dl.py`, `.work/repro_wiz*.py` — scripted first-run repros.
- `.work/freeze.log` / `.work/desktop-src.*` — the "process alive, zero
  windows, no port" run.
- `.work/loopcheck.py` / `.work/loopcheck.log` — double `webview.start()`
  probe.
- `.work/wizrun*.log` / `.work/wizrun*.err` — full wizard runs with logs.
- `.work/acc-wiz/`, `.work/wizaccel/` — the custom-dir first-run scratch
  roots (install + `paths.env`), reusable without re-downloading.

No real proxy IPs, credentials, tokens, Windows usernames or personal
absolute paths are recorded in this document.
