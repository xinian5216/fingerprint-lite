"""Assemble the Windows portable edition from the built desktop bundle.

The portable edition is the desktop bundle plus the bits that make it a
product a non-technical user can run: a double-click entry, a ``Data`` folder
that shows where user data lives, and a README that says what to do on first
run and on upgrade.

Run after (or through) ``scripts/build_desktop.py``:

    python scripts/build_portable.py

The assembly refuses an incomplete bundle — both executables and the full web
UI must be in it — so a stale or half-built bundle cannot quietly become a
release-shaped artifact.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "dist" / "camoufox-pm"
DIST = ROOT / "dist"

REQUIRED_FILES = (
    "FingerprintLite.exe",
    "camoufox-pm.exe",
    "_internal/camoufox_pm/webui/index.html",
)

DATA_README = """\
Fingerprint Lite data folder
============================

Everything this program saves lives here:
  profiles.db   SQLite database (profiles, groups, schedules, accounts)
  profiles\\     browser data for each profile (cookies, storage, logins)
  config.env    configuration, including the encryption key for proxy passwords
  logs\\         application logs (rotated, secrets redacted)

Back this folder up to back up your profiles. When upgrading the program,
keep this folder — replace only the program files around it.

If this folder cannot be written to, the program stops with a clear error
instead of silently moving your data somewhere else.
"""

PORTABLE_README = """\
Fingerprint Lite {version} — Windows portable edition
====================================================

Start: double-click FingerprintLite.exe. No Python, no Node, no installation.

First run
---------
The first start prepares the fixed Camoufox browser build ({browser_version}),
verified against its published SHA256. It is downloaded once (about 470 MB) into
this machine's browser cache. If that download is slow or blocked — some
networks throttle GitHub releases — either give the process a proxy
(HTTPS_PROXY=http://127.0.0.1:<port>) or use the offline ZIP steps below. A
failed download is not retried behind your back; the program says exactly what
to do instead. To install offline, download the official
ZIP named camoufox-{browser_version}-win.x86_64.zip from
https://github.com/daijro/camoufox/releases/tag/v{browser_version}
and either drop it into the Browser folder next to FingerprintLite.exe and
restart, or run:  camoufox-pm.exe browser install <path-to-zip>

Your data
---------
Everything you create lives in the Data folder (see Data\\README.txt). The
browser engine is kept separately and is never mixed into your profiles.

Upgrading
---------
Extract the new version and replace the program files; keep the Data folder
(and the Browser folder, if you created one). Your profiles, accounts and
settings carry over. Encrypted proxy passwords stay readable because the key
lives in Data\\config.env.

Licenses
--------
See LICENSE and THIRD-PARTY-NOTICES.md for project and bundled dependency
attribution. The separately downloaded Camoufox browser keeps its own notices.

Debugging
---------
FingerprintLite.exe runs without a console; its output goes to
Data\\logs\\console.log. For a console window and the CLI (user add, leases,
browser install/status), run camoufox-pm.exe instead — same backend, same
data folder. Only one copy runs at a time; a second start reports that the
program is already running.

Ports
-----
The app serves http://127.0.0.1:8000 by default. To change it, edit
Data\\config.env (CPM_PORT=...) or start camoufox-pm.exe with --port.
"""


def assert_bundle_complete(bundle_dir: Path) -> None:
    """Refuse to package anything missing an entry point or the web UI."""
    missing = [name for name in REQUIRED_FILES if not (bundle_dir / name).exists()]
    if missing:
        raise SystemExit(
            f"The desktop bundle at {bundle_dir} is incomplete — missing: "
            f"{', '.join(missing)}. Run scripts/build_desktop.py first."
        )


def assemble(bundle_dir: Path, output_dir: Path, version: str, browser_version: str) -> Path:
    """Copy the bundle into its portable folder and add the portable bits."""
    assert_bundle_complete(bundle_dir)
    target = output_dir / f"FingerprintLite-{version}-windows-x64"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(bundle_dir, target)

    (target / "Browser").mkdir(exist_ok=True)
    (target / "Browser" / "README.txt").write_text(
        "Drop the official camoufox browser ZIP here for an offline install.\n"
        "It is verified against a pinned SHA256 before anything is installed.\n",
        encoding="utf-8",
    )
    (target / "Data").mkdir(exist_ok=True)
    (target / "Data" / "README.txt").write_text(DATA_README, encoding="utf-8")
    for notice in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        shutil.copy2(ROOT / notice, target / notice)
    (target / "README-portable.txt").write_text(
        PORTABLE_README.format(version=version, browser_version=browser_version),
        encoding="utf-8",
    )
    return target


def zip_folder(folder: Path) -> Path:
    """One zip next to the folder, named like the folder."""
    archive = shutil.make_archive(str(folder), "zip", root_dir=folder.parent, base_dir=folder.name)
    return Path(archive)


def main() -> int:
    from importlib.metadata import version as pkg_version

    from camoufox_pm import browser_env

    app_version = pkg_version("camoufox-profile-manager")
    print("Building the desktop bundle...")
    import subprocess

    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_desktop.py")], check=True)

    folder = assemble(BUNDLE, DIST, app_version, browser_env.pin_version_string())
    archive = zip_folder(folder)
    print(f"Portable folder : {folder}")
    print(f"Portable zip    : {archive} ({archive.stat().st_size / 1_048_576:.1f} MB)")
    print(
        f"Folder size     : {sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()) / 1_048_576:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
