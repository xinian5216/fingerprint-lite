"""Diagnose browser-data persistence in the profile directory.

Writes a cookie and a localStorage marker in one launch, reads them back in the
next launch from the same user_data_dir, and lists the on-disk evidence.
"""

import asyncio
from pathlib import Path

from camoufox import AsyncCamoufox
from tests.browser.support import serve_local_sites

from camoufox_pm.core.models import BrowserSettings, Profile

TMP = Path(__file__).resolve().parent.parent / "data" / "diagnostics"


async def write(origin: str, data_dir: Path) -> None:
    profile = Profile(name="diag-persist", browser_settings=BrowserSettings(os="windows"))
    options = profile.to_camoufox_launch_options()
    options.update(headless=True, user_data_dir=str(data_dir))
    async with AsyncCamoufox(**options) as context:
        page = await context.new_page()
        await page.goto(origin, wait_until="domcontentloaded")
        await context.add_cookies([{"name": "script-cookie", "value": "kept", "url": origin}])
        await page.evaluate("document.cookie = 'doc-cookie=kept; path=/'")
        await page.evaluate("localStorage.setItem('marker', 'kept')")
        print("write: context.cookies() =", await context.cookies())
        print("write: document.cookie =", await page.evaluate("document.cookie"))
        print("write: localStorage =", await page.evaluate("localStorage.getItem('marker')"))


async def read(origin: str, data_dir: Path) -> None:
    profile = Profile(name="diag-persist", browser_settings=BrowserSettings(os="windows"))
    options = profile.to_camoufox_launch_options()
    options.update(headless=True, user_data_dir=str(data_dir))
    async with AsyncCamoufox(**options) as context:
        page = await context.new_page()
        await page.goto(origin, wait_until="domcontentloaded")
        print("read: context.cookies() =", await context.cookies())
        print("read: document.cookie =", await page.evaluate("document.cookie"))
        print("read: localStorage =", await page.evaluate("localStorage.getItem('marker')"))


async def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    data_dir = TMP / "persist"
    with serve_local_sites() as sites:
        await write(sites.first, data_dir)
        await read(sites.first, data_dir)

    print("--- files in profile dir ---")
    interesting = ("cookies.sqlite", "storage", "prefs.js", "places.sqlite", "sessionstore")
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and any(part in path.name for part in interesting):
            print(f"{path.relative_to(data_dir)}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    asyncio.run(main())
