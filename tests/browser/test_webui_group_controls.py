"""Opt-in browser regression coverage for hidden Groups/Excel UI and retained archives.

Run only with a local Next server. In one PowerShell window, start it with
``cd web; npm run dev -- --hostname 127.0.0.1 --port 3000``. In another, set
``$env:CPM_WEBUI_TEST_URL = 'http://127.0.0.1:3000'`` and run
``uv run pytest -q tests/browser/test_webui_group_controls.py``. This browser
test is explicitly invoked and is not part of the project's Windows CI suite.
"""

import json
import os
from urllib.parse import urlsplit

import pytest
from camoufox import AsyncCamoufox

from tests.browser.support import offline_launch

WEBUI_URL = os.environ.get("CPM_WEBUI_TEST_URL")
GROUP_ID = "stored-group"
PROFILE_ID = "profile-with-stored-group"


@pytest.mark.browser
@pytest.mark.skipif(not WEBUI_URL, reason="set CPM_WEBUI_TEST_URL to a running local web UI")
@pytest.mark.asyncio
async def test_group_and_excel_controls_are_hidden_without_affecting_archives():
    """Group/Excel controls stay hidden while whole-profile archives remain usable."""
    profile = {
        "id": PROFILE_ID,
        "name": "existing account",
        "group": GROUP_ID,
        "status": "active",
        "browser_settings": {
            "os": "windows",
            "screen": "1280x720",
            "languages": ["en-US", "en"],
            "window_width": 1280,
            "window_height": 720,
            "webrtc_mode": "replace",
            "stable_canvas": False,
        },
        "proxy_config": None,
        "notes": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "row_version": 1,
    }
    api_group = {
        "id": GROUP_ID,
        "name": "API fixture group",
        "description": "Mock response if the UI requests groups",
        "created_at": "2026-01-01T00:00:00Z",
        "profile_count": 1,
    }
    update_payloads = []
    group_api_requests = []
    violations = []

    async def mock_api(route):
        request = route.request
        path = urlsplit(request.url).path
        method = request.method

        if path == "/api/v1/auth/session":
            body = {"user_auth_enabled": False, "authenticated": False, "username": None}
        elif path == "/api/v1/profiles" and method == "GET":
            body = {
                "profiles": [profile],
                "total": 1,
                "page": 1,
                "per_page": 100,
                "has_next": False,
                "has_prev": False,
            }
        elif path == f"/api/v1/profiles/{PROFILE_ID}" and method == "PUT":
            update_payloads.append(json.loads(request.post_data or "{}"))
            body = profile
        elif path == "/api/v1/groups" or path.startswith("/api/v1/groups/"):
            group_api_requests.append((method, path))
            body = {"groups": [api_group], "total": 1}
        elif path == "/api/v1/browsers/active":
            body = {"active_browsers": [], "count": 0}
        elif path == "/api/v1/system/config":
            body = {
                "data": {
                    "version": "test",
                    "host": "127.0.0.1",
                    "port": 8000,
                    "database_path": ":memory:",
                    "api_key_set": False,
                    "user_auth_enabled": False,
                    "encryption_enabled": False,
                    "cors_origins": [],
                    "camoufox_available": True,
                    "uptime_seconds": 60,
                }
            }
        elif path == "/api/v1/system/status":
            body = {
                "total_profiles": 1,
                "active_profiles": 1,
                "running_browsers": 0,
                "total_groups": 1,
                "system_load": 0,
                "memory_usage": 0,
                "disk_usage": 0,
                "uptime_seconds": 60,
            }
        else:
            # Every API request is intercepted, including endpoints not needed
            # by this flow, so the browser never depends on a real backend.
            body = {}

        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(body),
        )

    async with AsyncCamoufox(**offline_launch({"headless": True})) as browser:
        page = await browser.new_page()
        await page.route("**/api/v1/**", mock_api)
        base_url = WEBUI_URL.rstrip("/")

        await page.goto(f"{base_url}/")
        await page.get_by_role("cell", name="existing account", exact=True).wait_for()

        if await page.get_by_role("link", name="Groups", exact=True).count():
            violations.append("group navigation is visible")
        if await page.locator("main table thead th").filter(has_text="Group").count():
            violations.append("profile table exposes a Group column")
        if await page.locator('[title="Export to Excel"]').count():
            violations.append("Excel export control is visible")
        if await page.locator('[title="Import from Excel"]').count():
            violations.append("Excel import control is visible")
        if await page.locator('input[type="file"][accept=".xlsx,.xls"]').count():
            violations.append("Excel import file input is present")
        if await page.locator('[title="Import a profile archive"]').count() != 1:
            violations.append("profile archive import control is missing")
        if await page.locator('input[type="file"][accept=".zip"]').count() != 1:
            violations.append("profile archive file input is missing")

        await page.get_by_role("button", name="Actions for existing account").click()
        await page.get_by_role("menuitem", name="Edit", exact=True).click()
        await page.get_by_role("heading", name="Edit profile", exact=True).wait_for()
        if await page.locator("#pf-group").count():
            violations.append("profile form exposes a group selector")

        await page.locator("#pf-name").fill("renamed account")
        async with page.expect_response(
            lambda response: (
                response.request.method == "PUT"
                and urlsplit(response.url).path == f"/api/v1/profiles/{PROFILE_ID}"
            )
        ):
            await page.get_by_role("button", name="Save changes", exact=True).click()
        await page.get_by_role("dialog").wait_for(state="detached")

        if not update_payloads:
            violations.append("editing another field did not send a profile update")
        else:
            if update_payloads[-1].get("name") != "renamed account":
                violations.append("profile edit payload omitted the changed name")
            if "group" in update_payloads[-1]:
                violations.append("profile edit payload includes the group field")
        if group_api_requests:
            violations.append(f"hidden group UI made group API requests: {group_api_requests}")

        await page.get_by_role("button", name="Actions for existing account").click()
        if await page.get_by_role("menuitem", name="Export…", exact=True).count() != 1:
            violations.append("profile archive export action is missing")
        await page.keyboard.press("Escape")

        await page.get_by_role("link", name="Settings", exact=True).click()
        await page.get_by_role("heading", name="Settings", exact=True).wait_for()
        await page.get_by_text("Running browsers", exact=True).wait_for()
        if await page.locator("main").get_by_text("Groups", exact=True).count():
            violations.append("Settings exposes the group count")

        group_page_response = await page.goto(f"{base_url}/groups/")
        if group_page_response is None or group_page_response.status != 404:
            status = group_page_response.status if group_page_response else "no response"
            violations.append(f"the group route is still served (HTTP {status})")
        if await page.locator("main").get_by_role("heading", name="Groups", exact=True).count():
            violations.append("the group management page is visible")

    assert not violations, "Profile UI regression:\n- " + "\n- ".join(violations)
