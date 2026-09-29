"""Proxy diagnosis with out-of-process proxies (the deployment-shaped case).

Uses the standalone HTTP proxy on 127.0.0.1:8899 and SOCKS5 proxy on
127.0.0.1:8898, both started as separate processes, so the browser and the
proxy do not share an event loop.
"""

import asyncio
import uuid
from pathlib import Path

from camoufox import AsyncCamoufox
from tests.browser.support import offline_launch

from camoufox_pm.core.models import BrowserSettings, Profile, ProxyConfig, ProxyType

TMP = Path(__file__).resolve().parent.parent / "data" / "diagnostics2"


async def visit(proxy: dict | None, label: str) -> None:
    profile = Profile(
        name=f"diag2-{label}",
        browser_settings=BrowserSettings(os="windows"),
        proxy=ProxyConfig(**proxy) if proxy else None,
    )
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(TMP / str(uuid.uuid4())[:8])
    print(f"--- {label}: proxy option = {options.get('proxy')}")
    try:
        async with AsyncCamoufox(**options) as context:
            page = await context.new_page()
            try:
                response = await page.goto(
                    "https://api.ipify.org", wait_until="domcontentloaded", timeout=45000
                )
                body = (await page.content()).strip()[:120]
                print(
                    f"{label}: loaded status={response.status if response else None} body={body!r}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{label}: goto FAILED -> {type(exc).__name__}: {str(exc)[:180]}")
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: launch FAILED -> {type(exc).__name__}: {str(exc)[:180]}")


async def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    await visit(
        {
            "type": ProxyType.HTTP,
            "server": "127.0.0.1:8899",
            "username": "smoke",
            "password": "s3cret",
        },
        "http-standalone",
    )
    await visit({"type": ProxyType.SOCKS5, "server": "127.0.0.1:8898"}, "socks-standalone")
    await visit(None, "direct-control")


if __name__ == "__main__":
    asyncio.run(main())
