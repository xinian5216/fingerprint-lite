# First-run → desktop handoff

- Date: 2026-09-30
- Branch: `test/phase5a-acceptance`; existing Draft PR #8 remains unmerged.
- Starting HEAD: `683d6279dba01a73e1ea79eb7f200a599e4b55e4`.
- Bug status: **OPEN / WIP — code tested, packaged Windows GUI acceptance pending**.

## Report and evidence boundary

The user observed the first-start wizard successfully installing Camoufox and
GeoIP, then closing without a management window. The previous investigation
recorded a source-path reproduction with a live process, zero windows and no
listener. It implicated a second `webview.start()` in the same process, but
was not a packaged Windows reproduction. That remains a hypothesis supported
by source evidence, not a completed real-machine diagnosis.

This follow-up runs in Linux without a Windows desktop control surface.
Unit tests, a Windows build and a console smoke check cannot prove that a real
wizard hands off to a visible, working manager. Do not mark this bug FIXED or
split out a fix PR until the acceptance below succeeds.

## Implementation

Every successful **frozen windowed** wizard completion now starts a fresh
`FingerprintLite.exe --desktop`, including an explicit setup rerun or a retry
where `paths.env` already exists. The parent exits before portable bootstrap,
instance-lock acquisition or a second GUI loop. Source and console starts
keep their existing behavior, even with a sibling executable in the folder.

Relaunch preserves runtime arguments such as `--port` and `--portable`, and
consumes `--desktop`, `--wizard`, and `--wizard-answers` (including its value)
so the child cannot recursively rerun setup. Missing executable/config and
spawn failures report a visible error and exit 3; they never silently fall
through into the already-used GUI process. Successful setup data is kept.

The wizard worker starts while the Start lock is held, closing the gap where
another press could see an assigned but not-yet-live worker.

The windowed log capture now receives Loguru explicitly. Parent/child PIDs,
wizard start/skip/completion, server readiness/port and GUI loop start/end are
recorded. A `GUI loop starting` line proves only a request to open the window,
not a visible or successfully rendered manager. Initial parent logs can be
under program-root `Data/logs`, while the child logs use the chosen data root.

## Completed local validation

- `uv run --locked ruff check src tests`: PASS.
- `uv run --locked ruff format --check src tests`: PASS.
- `uv run --locked mypy src/camoufox_pm`: PASS (40 source files).
- `uv run --locked pytest -m "not browser" -q`: **524 passed, 2 skipped,
  25 deselected**. Browser launch tests were not run.
- The primary relaunch test begins without `paths.env`, lets the wizard write
  it, checks the exact preserved `--port 9123` command/cwd, and forbids parent
  bootstrap or a second desktop loop. The child-start test checks wizard skip,
  real portable bootstrap and instance-lock release with a mocked desktop.
- Additional tests cover setup-flag consumption, existing paths, incomplete
  handoff, spawn failure, cancellation, setup failure, source/console starts
  and persistence of Loguru handoff diagnostics.
- CLI fixtures now isolate working directories, path variables and process
  flags and stub the desktop, avoiding unintended native loops or databases.
  Lock tests with the known-live current process stub only PID liveness:
  this Linux sandbox exposes a different `/proc` PID namespace to psutil.

## Windows artifact build

`.github/workflows/first-run-portable.yml` runs only for the existing recovery
branch PR (or an explicit dispatch). It checks locked Windows dependencies,
lint/types/tests, parses the PowerShell recorder, builds the real portable
ZIP and smoke-tests `camoufox-pm.exe browser status`. Its artifact contains:

- `FingerprintLite-*-windows-x64.zip`;
- `build-info.json` with source commit and ZIP SHA256;
- `record_first_run.ps1`.

The artifact is retained for 7 days. It is a WIP build, not a tag or Release.
The console smoke runs after ZIP assembly so its generated Data files do not
contaminate the fresh-install ZIP. Build success is not GUI acceptance.

## Required real Windows acceptance

From a clean PowerShell session, run the recorder from the downloaded artifact:

```powershell
powershell -File .\record_first_run.ps1 -PortableZip .\FingerprintLite-0.1.0a1-windows-x64.zip
```

Use the actual ZIP filename in the artifact. The recorder creates a unique
`.work/first-run-*` directory and refuses a non-fresh ZIP or an occupied test
port. It launches the real windowed entry with no scripted wizard answers.
Complete the visible wizard, using a real verified browser download or the
official offline ZIP, and keep the manager open. Do not select a production
Data directory for this acceptance run.

The record must show:

1. Actual Wizard completion and parent exit code 0.
2. A different manager PID matching the recorded handoff, with wizard skipped.
3. A visible `Fingerprint Lite` window, its own HTTP listener, successful
   page response, and stability for at least 10 seconds.
4. Human confirmation that the manager rendered and works normally.
5. Normal close, no matching app process or test-port listener, and no
   instance lock left behind.

The recorder stores process/window/port snapshots, combined console logs,
ZIP hash and `result.json` locally. A timeout or failed close preserves the
processes and all data for diagnosis; nothing is force-killed. Review private
paths in logs before sharing. Repeat on three fresh extracted copies, plus a
normal second launch, before treating the handoff as stable and changing the
status to FIXED. A single observed pass is evidence, not full stability proof.

If it fails, compare `parent_pid`/`child_pid` with the process snapshots. Check
whether the parent exited, whether the child skipped setup and got its lock,
whether its own port is listening, and whether a window exists. Continue from
that evidence on this branch; do not speculate or split PRs first.

## Protection and rollback

No main merge, new PR, version/tag/Release, browser pin change or production
profile migration is part of this fix. The Windows acceptance uses a new
extraction and a separate port, so existing installations need no downtime.
To roll back the program, retain the existing Data/Browser directories and
replace only the program files with the previous build; never delete
`config.env` or its encryption key. Source rollback is a normal revert of the
follow-up commit, not a force-push or a reset of shared history.
