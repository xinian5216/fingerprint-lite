"""Location failures must be readable in both UI languages, including saved legacy text."""

import os

import pytest
from camoufox import AsyncCamoufox
from playwright.async_api import expect

URL = os.getenv("CPM_WEBUI_TEST_URL")


@pytest.mark.browser
@pytest.mark.skipif(not URL, reason="Set CPM_WEBUI_TEST_URL to a running local web UI")
@pytest.mark.asyncio
async def test_location_findings_follow_the_ui_language():
    finding = {"level": "info", "field": "proxy", "message": "English API fallback"}

    async def answer(route):
        await route.fulfill(
            json={
                "reachable": True,
                "error": None,
                "latency_ms": 12,
                "location": {"ip": "203.0.113.7"},
                "findings": [finding],
                "checked_at": None,
            }
        )

    async with AsyncCamoufox(headless=True, config={"showcursor": False}) as browser:
        page = await browser.new_page()
        await page.route("**/api/v1/proxy/check", answer)
        await page.goto(URL)
        await page.get_by_role("button", name="新建 Profile", exact=True).click()
        await page.locator("#pf-proxy-server").fill("127.0.0.1:9")
        cases = [
            ("geoip_address_unknown", "数据库未收录该出口地址"),
            ("geoip_database_missing", "地理位置数据库缺失"),
            ("geoip_database_invalid", "地理位置数据库已损坏"),
            ("geoip_unavailable", "暂时无法读取地理位置数据库"),
        ]
        for code, text in cases:
            finding["code"] = code
            await page.get_by_role("button", name="检测代理", exact=True).click()
            await expect(page.get_by_text(text, exact=False)).to_be_visible()
            await expect(page.get_by_text("English API fallback", exact=True)).to_have_count(0)
        finding.pop("code")
        finding["message"] = (
            "The proxy works, but its address could not be placed on the map. Run 'camoufox fetch' to install the location database."
        )
        await page.get_by_role("button", name="检测代理", exact=True).click()
        await expect(page.get_by_text("暂时无法读取地理位置数据库", exact=False)).to_be_visible()
        await expect(page.get_by_text("camoufox fetch", exact=False)).to_have_count(0)

        await page.evaluate("localStorage.setItem('fingerprint-lite.ui-lang', 'en')")
        await page.reload()
        await page.get_by_role("button", name="New profile", exact=True).click()
        await page.locator("#pf-proxy-server").fill("127.0.0.1:9")
        finding["code"] = "geoip_address_unknown"
        await page.get_by_role("button", name="Check proxy", exact=True).click()
        await expect(
            page.get_by_text("Its country and timezone could not be confirmed.", exact=False)
        ).to_be_visible()
