Work folder — dev/test scratch only
===================================

Everything developers and tests generate lives here, never in the user's
profile and never as real user data:

  uv-cache/       uv cache               (export UV_CACHE_DIR=.work/uv-cache)
  npm-cache/      npm cache              (scripts/build_webui.py sets it)
  pyinstaller/    PyInstaller workpath   (scripts/build_desktop.py sets it)
  pytest-tmp/     pytest basetemp        (pyproject addopts)
  browser-cache/  Camoufox browser + GeoIP cache used by tests
  tmp/            anything else temporary

The browser cache is populated the same way as for users (this folder is what
tests resolve the browser from):

    uv run camoufox-pm browser install        # or drop the official ZIP in
    .../browser-cache/../Browser/ and install from it

Everything here is disposable and git-ignored.
