# Public Repository Readiness — Fingerprint Lite v0.1.0-alpha.1 snapshot

- Snapshot source: private `xinian5216/fingerprint-lite`, branch `release/v0.1.0-alpha.1 @ ba57e89e2184f4cffcb3b0cd1fcf7041a11af844` (fetched 2026-09-30, `git fetch origin`, tip matches `ls-remote`).
- Export method: `git archive origin/release/v0.1.0-alpha.1` into this independent directory. No `.git` history, no `.work` data (only the tracked `.work/README.txt` placeholder), no caches, build outputs, profiles, databases, logs, or downloaded browser archives.
- Private repository was not modified: no rebase, no filter-repo, no force-push, no tag/branch deletion. All private history remains as the read-only archive.

## Public content (this directory)

- Product source: `src/`, `web/`, `packaging/`, `scripts/`, `smoke/`, `tests/`, `examples/`.
- Docs: `README.md`, `README.ru.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `docs/` (product docs; `docs/superpowers/` excluded, see below).
- Build metadata: `pyproject.toml` (`0.1.0-alpha.1`, URLs already `xinian5216/fingerprint-lite`), `uv.lock`, `web/package.json`, `Dockerfile`, `.env.example`, `.github/workflows/` (standard runners only).
- License/attribution: `LICENSE` (MIT, unchanged), `THIRD-PARTY-NOTICES.md` (Camoufox Python MIT + browser MPL-2.0 + upstream project attribution).

## Excluded content (stays in the private archive only)

- Internal development reports: `HANDOFF.md`, `PINS.md`, `PHASE3C-ACCEPTANCE.md`, `PHASE3C-MANUAL-CHECKLIST.md`, `PHASE3D-ACCEPTANCE.md`, `PHASE4A-ACCEPTANCE.md`, `WINDOWS-VALIDATION.md`.
- Internal planning notes: `docs/superpowers/`.
- Reason: acceptance logs contain a real proxy exit IP (redacted here), geo/RTT details, and local test paths not suitable as public documentation. The private repo retains them unmodified.
- Never exported by construction: `.work/`, `Data/`, `Browser/`, `Temp/`, `*.db`, `*.log`, `dist/`, downloaded `camoufox-*.zip`, Alpha artifacts.

## Changes made inside this snapshot only

1. Deleted the 7 internal report files + `docs/superpowers/` (desensitization by exclusion).
2. `.gitignore`: added explicit `/Data/`, `/Browser/`, `/Temp/`, `/evidence/`, `/.acceptance/`-style local evidence, `*.zip` / `camoufox-*.zip` / `FingerprintLite-*-windows-x64/` guards (case-explicit; base `gitignore` matching is case-sensitive on Linux).
3. `packaging/camoufox-pm.spec`: macOS `bundle_identifier` → `com.github.xinian5216.fingerprint-lite` (was upstream `com.github.polyackiy.camoufox-pm`).
4. No source, version, or Camoufox pin changes. `README.md`/`README.ru.md`, `pyproject.toml`, issue templates already pointed at `xinian5216/fingerprint-lite`; historical `CHANGELOG.md` links to `polyackiy/camoufox-profile-manager` are kept as upstream history with attribution in `THIRD-PARTY-NOTICES.md`.

## License status

- `LICENSE`: MIT, `Copyright (c) 2026 Camoufox Profile Manager Contributors`, unchanged.
- `THIRD-PARTY-NOTICES.md`: retains upstream MIT notice; records `camoufox` Python 0.5.6 (MIT metadata) and official Camoufox browser `152.0.4-beta.30` (upstream MPL-2.0, engine not bundled).
- `README.md` License section links MIT + third-party notes + `daijro/camoufox`.

## Scan results (this snapshot, 2026-09-30)

- Token/key patterns (`ghp_|gho_|github_pat_|AKIA|BEGIN *PRIVATE KEY|Bearer|Cookie=`): **0 hits**.
- PII/path patterns (Windows user profile paths, local doc roots, real proxy exit octets, machine digits): **0 hits**.
- Artifact patterns (`*.db`, `*.sqlite*`, `*.log`, `.env`, `config.env`, `paths.env`, `*.zip`): **0 files** (only `.env.example` template remains).
- Remaining credential-like strings are test-only fixtures, same as private audit: `smoke/*` loopback proxy `smoke/s3cret @ 127.0.0.1:8899`, `tests/**/top-secret*` placeholders, `user passwd` CLI subcommand names in `docs/cli.md` / `src/camoufox_pm/cli.py`. No live hosts, no real passwords, no Fernet keys.

## Still required (manual, not done here)

1. Create the new public repository (empty, no history import) and push this snapshot as its initial commit from a clean `git init` here.
2. Do NOT change the current private repository visibility; do NOT delete its branches/tags.
3. On the public repo: set default branch, enable Secret Scanning + Push Protection, add branch protection + tag protection for `v*`, restrict `release.yml`/`desktop.yml` triggers, keep `PUBLISH_TO_PYPI` off until separately approved.
4. Commit-author emails from the private history (`polyackiy@gmail.com`, `admin@MacBook-Pro-Admin.local`) are NOT included here (no history exported) — no action needed for this snapshot, but do not push private history to the public repo later.
