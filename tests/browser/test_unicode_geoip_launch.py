"""A real browser launch must derive geography from a Unicode-path database."""

import shutil
from pathlib import Path

import pytest
from camoufox import geolocation

from camoufox_pm import browser_env
from camoufox_pm.core import proxy_check
from camoufox_pm.core.browser_session import BrowserSessionManager
from camoufox_pm.core.models import Profile
from tests.browser.support import timezone_a_page_sees


@pytest.mark.browser
@pytest.mark.asyncio
async def test_managed_browser_uses_geoip_from_a_chinese_directory(tmp_path, monkeypatch):
    folder = tmp_path / "中文目录" / "Browser/cache/geoip/mmdb"
    folder.mkdir(parents=True)
    fixture = Path(__file__).resolve().parents[1] / "fixtures/maxmind/GeoIP2-City-Test.mmdb"
    shutil.copyfile(fixture, folder / "test-city-combined.mmdb")
    config = {
        "name": "test-city",
        "urls": {"combined": ["not-used"]},
        "paths": {
            "iso_code": "country.iso_code",
            "longitude": "location.longitude",
            "latitude": "location.latitude",
            "timezone": "location.time_zone",
        },
    }
    monkeypatch.setattr(geolocation, "MMDB_DIR", folder)
    monkeypatch.setattr(geolocation, "load_geoip_config", lambda: config)
    monkeypatch.setattr(
        geolocation, "download_mmdb", lambda: pytest.fail("Database already exists")
    )
    monkeypatch.setattr(browser_env, "default_offline_dir", lambda: tmp_path / "runtime-cache")

    # This address is in the synthetic test database. Naming it avoids sending
    # a real proxy request while preserving the actual GeoIP launch path.
    ip = "81.2.69.142"
    location = proxy_check.locate(ip)
    assert location.country == "GB"
    assert location.timezone == "Europe/London"

    profile = Profile(name="Unicode GeoIP")
    options = profile.to_camoufox_launch_options()
    options.update(headless=True, geoip=ip, user_data_dir=str(tmp_path / "已有配置"))
    manager = BrowserSessionManager()
    session = await manager.launch(profile.id, options)
    try:
        context = session.camoufox.browser
        page = await context.new_page()
        assert await timezone_a_page_sees(page) == "Europe/London"
        assert manager.is_running(profile.id)
    finally:
        await manager.close(profile.id)
