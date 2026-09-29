"""PyInstaller entry point for the windowed desktop app (FingerprintLite.exe)."""

from camoufox_pm.windowed import main

if __name__ == "__main__":
    raise SystemExit(main())
