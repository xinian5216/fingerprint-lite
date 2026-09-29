"""Diagnose proxy behaviour through a real browser (in-process proxies).

Prints, for each scenario: the proxy's request log, any tunnel errors, and the
page outcome. Used to judge the smoke-test failures.
"""

import asyncio
from pathlib import Path

from camoufox import AsyncCamoufox
from tests.browser.support import offline_launch

from camoufox_pm.core.models import BrowserSettings, Profile, ProxyConfig, ProxyType
from smoke.proxy_server import HttpConnectProxy, Socks5Proxy

TMP = Path(__file__).resolve().parent.parent / "data" / "diagnostics"


async def visit(options, label, timeout=30000):
    options = dict(options)
    options["headless"] = True
    print(f"--- launching {label} ---")
    try:
        async with AsyncCamoufox(**options) as context:
            page = await context.new_page()
            try:
                response = await page.goto("https://api.ipify.org", timeout=timeout)
                body = (await page.content()).strip()[:160]
                print(
                    f"{label}: loaded status={response.status if response else None} body={body!r}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{label}: goto FAILED -> {type(exc).__name__}: {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: launch FAILED -> {type(exc).__name__}: {str(exc)[:200]}")


async def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)

    async with HttpConnectProxy(user="smoke", password="s3cret") as proxy:
        profile = Profile(
            name="diag-http",
            browser_settings=BrowserSettings(os="windows"),
            proxy=ProxyConfig(
                type=ProxyType.HTTP,
                server=f"127.0.0.1:{proxy.port}",
                username="smoke",
                password="s3cret",
            ),
        )
        options = offline_launch(profile.to_camoufox_launch_options())
        options["user_data_dir"] = str(TMP / "http")
        # Print the browser's proxy preferences for evidence.
        opts_with_proxy = profile.to_camoufox_launch_options()
        print("camoufox proxy option:", opts_with_proxy.get("proxy"))
        await visit(options, "http-proxy-with-creds")
        print("http proxy entries:", [(e.method, e.target, e.authorized) for e in proxy.entries])
        print("http proxy errors:", proxy.errors)

    async with Socks5Proxy() as socks:
        profile = Profile(
            name="diag-socks",
            browser_settings=BrowserSettings(os="windows"),
            proxy=ProxyConfig(type=ProxyType.SOCKS5, server=f"127.0.0.1:{socks.port}"),
        )
        options = offline_launch(profile.to_camoufox_launch_options())
        options["user_data_dir"] = str(TMP / "socks")
        print("camoufox proxy option:", options.get("proxy"))
        await visit(options, "socks5-no-creds")
        print("socks entries:", [(e.atyp, e.target) for e in socks.entries])
        print("socks errors:", socks.errors)

    # Control without any proxy.
    profile = Profile(name="diag-direct", browser_settings=BrowserSettings(os="windows"))
    options = offline_launch(profile.to_camoufox_launch_options())
    options["user_data_dir"] = str(TMP / "direct")
    await visit(options, "direct")


if __name__ == "__main__":
    asyncio.run(main())
