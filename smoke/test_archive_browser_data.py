"""A profile archive is only a real backup if the browser data comes back.

Settings-only round-trips are easy; the value of the archive is the warmed-up
identity — cookies and storage that took months to earn. This drives the real
``profile_archive`` machinery over a profile directory that holds actual
browser data, then relaunches from the restored copy and reads it back.
"""

import time

import pytest

from camoufox_pm.core import profile_archive
from camoufox_pm.core.models import BrowserSettings, Profile


def profile_options():
    return Profile(
        name="archive-data", browser_settings=BrowserSettings(os="windows")
    ).to_camoufox_launch_options()


async def open_context(options, user_data_dir):
    from camoufox import AsyncCamoufox

    launch = dict(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    return AsyncCamoufox(**launch)


@pytest.mark.asyncio
async def test_an_archive_carries_real_browser_data(tmp_path, local_sites):
    source_dir = tmp_path / "warmed-up"
    launch = profile_options()
    expires = time.time() + 86400

    # Warm the profile the way a real one gets warm: a login cookie and storage.
    async with await open_context(launch, source_dir) as context:
        page = await context.new_page()
        await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=45000)
        await context.add_cookies(
            [{"name": "login", "value": "kept", "url": local_sites.first, "expires": expires}]
        )
        await page.evaluate("localStorage.setItem('marker', 'kept')")

    # Back it up with the real archiver — record + the whole data directory.
    profile = Profile(name="warmed-up", browser_settings=BrowserSettings(os="windows"))
    archive = tmp_path / "backup.camoufox.zip"
    profile_archive.export_profile(profile, source_dir, archive)
    assert archive.stat().st_size > 0

    record = profile_archive.read_archive(archive)
    assert record["name"] == "warmed-up"

    restored_dir = tmp_path / "restored"
    profile_archive.extract_data(archive, restored_dir)
    assert any(restored_dir.iterdir()), "the data directory must come back"

    # Relaunch from the restored copy: the identity must still be logged in.
    async with await open_context(launch, restored_dir) as context:
        page = await context.new_page()
        await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=45000)
        cookies = {c["name"]: c["value"] for c in await context.cookies()}
        marker = await page.evaluate("localStorage.getItem('marker')")

    assert cookies.get("login") == "kept", f"the login cookie did not survive: {cookies}"
    assert marker == "kept", f"localStorage did not survive: {marker!r}"
