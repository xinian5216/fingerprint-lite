# PyInstaller spec for the Fingerprint Lite desktop app.
#
# Build (after `python scripts/build_webui.py`):
#   pyinstaller packaging/camoufox-pm.spec --noconfirm --clean
#
# Produces dist/camoufox-pm/ (a standalone bundle) with two entries that share
# one backend, one web UI and one browser install: `FingerprintLite.exe` is the
# windowed double-click door, `camoufox-pm.exe` keeps the console for the CLI
# and for debugging. On macOS it also produces a .app.
# The Camoufox browser binary is NOT bundled — it is pinned, verified and
# fetched at first run (see src/camoufox_pm/browser_env.py).
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is this file's directory (packaging/); ROOT is the repository root.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "src", "camoufox_pm", "webui"), "camoufox_pm/webui")]
binaries = []
hiddenimports = collect_submodules("uvicorn")

# These packages ship data files (fingerprint datapoints, language tags, …) that
# PyInstaller does not pick up automatically.
for _pkg in (
    "camoufox",
    "browserforge",
    "apify_fingerprint_datapoints",
    "language_tags",
    "ua_parser",
):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [os.path.join(SPECPATH, "launch.py"), os.path.join(SPECPATH, "launch_windowed.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)

# One Analysis, two executables: the scripts come out of the TOC in input
# order, so split them by name rather than trusting the position.
_windowed_scripts = [entry for entry in a.scripts if "launch_windowed" in str(entry[0])]
_console_scripts = [entry for entry in a.scripts if "launch_windowed" not in str(entry[0])]

exe_console = EXE(
    pyz,
    _console_scripts,
    [],
    exclude_binaries=True,
    name="camoufox-pm",
    # Deliberate: this binary is also the CLI (`camoufox-pm --port ...`, `user
    # add`, ...), and a windowed build on Windows has no stdout, which loses the
    # server log and can break writes to it. The double-click door is the
    # windowed FingerprintLite.exe below.
    console=True,
)
exe_windowed = EXE(
    pyz,
    _windowed_scripts,
    [],
    exclude_binaries=True,
    name="FingerprintLite",
    console=False,
)
coll = COLLECT(
    exe_console,
    exe_windowed,
    a.binaries,
    a.datas,
    name="camoufox-pm",
)
app = BUNDLE(
    coll,
    name="Fingerprint Lite.app",
    bundle_identifier="com.github.xinian5216.fingerprint-lite",
)
