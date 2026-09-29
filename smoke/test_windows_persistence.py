"""Windows smoke: the persistent profile directory keeps browser data.

The manager launches with persistent_context=True against a per-profile
user_data_dir. This proves the mechanism it relies on, and records the one
measured limit: session-only cookies (no expiry) do not survive a restart,
because Camoufox configures `browser.sessionstore.privacy_level = 2`.
"""

import time

import pytest
from camoufox import AsyncCamoufox

from camoufox_pm.core.models import BrowserSettings, Profile


async def open_context(options, user_data_dir):
    launch = dict(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    return AsyncCamoufox(**launch)


def profile_options():
    return Profile(
        name="win-persist", browser_settings=BrowserSettings(os="windows")
    ).to_camoufox_launch_options()


@pytest.mark.asyncio
async def test_persistent_cookie_and_local_storage_survive_a_relaunch(tmp_path, local_sites):
    data_dir = tmp_path / "persist"
    launch = profile_options()
    expires = time.time() + 86400

    async with await open_context(launch, data_dir) as context:
        page = await context.new_page()
        await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=45000)
        await context.add_cookies(
            [
                {
                    "name": "persistent-cookie",
                    "value": "kept",
                    "url": local_sites.first,
                    "expires": expires,
                }
            ]
        )
        await page.evaluate("localStorage.setItem('smoke-marker', 'kept')")
        assert (await page.evaluate("localStorage.getItem('smoke-marker')")) == "kept"

    async with await open_context(profile_options(), data_dir) as context:
        page = await context.new_page()
        await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=45000)
        cookies = {c["name"]: c["value"] for c in await context.cookies()}
        marker = await page.evaluate("localStorage.getItem('smoke-marker')")

    assert cookies.get("persistent-cookie") == "kept", f"cookie was not persisted: {cookies}"
    assert marker == "kept", f"localStorage was not persisted: {marker!r}"


@pytest.mark.asyncio
async def test_session_only_cookies_do_not_survive_a_restart(tmp_path, local_sites):
    """Measured behaviour, not a requirement: Camoufox does not save sessions.

    `browser.sessionstore.privacy_level = 2` (settings/camoufox.cfg) means session
    cookies are not restored after the browser closes. Sites that keep a login in
    a session-only cookie will ask again after a restart.
    """
    data_dir = tmp_path / "session-cookie"
    launch = profile_options()

    async with await open_context(launch, data_dir) as context:
        page = await context.new_page()
        await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=45000)
        await context.add_cookies(
            [{"name": "session-cookie", "value": "kept", "url": local_sites.first}]
        )
        assert any(c["name"] == "session-cookie" for c in await context.cookies())

    async with await open_context(profile_options(), data_dir) as context:
        page = await context.new_page()
        await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=45000)
        cookies = {c["name"]: c["value"] for c in await context.cookies()}

    assert "session-cookie" not in cookies, f"session cookie unexpectedly survived: {cookies}"
