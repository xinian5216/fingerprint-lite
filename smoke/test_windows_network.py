"""Windows smoke: proxy paths, proxy failure, DNS and WebRTC surface.

The local proxies in smoke/proxy_server.py make every check observable:
credentials, the CONNECT target (which answers "who resolved DNS"), and the
failure mode of a proxy that is down.

Network-dependent assertions are limited to endpoints already confirmed
reachable from this machine (api.ipify.org). A failing load is reported as a
failure, not skipped.
"""

import re

import pytest
from camoufox import AsyncCamoufox
from tests.browser.support import offline_launch

from camoufox_pm.core.models import BrowserSettings, Profile, ProxyConfig, ProxyType
from smoke.proxy_server import HttpConnectProxy, Socks5Proxy

PRIVATE = re.compile(
    r"(^|[^\d])(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|127\.\d+\.\d+\.\d+|fe80::|fc[0-9a-f]{2}:|fd[0-9a-f]{2}:)",
    re.IGNORECASE,
)

ICE = """() => new Promise((resolve) => {
  const out = [];
  if (typeof RTCPeerConnection === 'undefined') { resolve(['NO_RTC']); return; }
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.createDataChannel('probe');
  pc.onicecandidate = (e) => { if (e.candidate) out.push(e.candidate.candidate); };
  pc.createOffer().then((o) => pc.setLocalDescription(o)).catch((e) => out.push('ERROR:' + e.message));
  setTimeout(() => { try { pc.close(); } catch (e) {} resolve(out); }, 3000);
})"""


async def visit(options, user_data_dir, url, timeout=45000):
    launch = offline_launch(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            body = (await page.content())[:200]
            return {"ok": True, "status": response.status if response else None, "body": body}
        except Exception as exc:  # noqa: BLE001 - the failure itself is the observation
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


async def gather_ice(options, user_data_dir):
    launch = offline_launch(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        return await page.evaluate(ICE)


@pytest.mark.asyncio
async def test_http_proxy_with_credentials_is_used_for_https(tmp_path):
    """The manager's proxy dict (with username/password) must reach the browser."""
    async with HttpConnectProxy(user="smoke", password="s3cret") as proxy:
        profile = Profile(
            name="win-proxy",
            browser_settings=BrowserSettings(os="windows"),
            proxy=ProxyConfig(
                type=ProxyType.HTTP,
                server=f"127.0.0.1:{proxy.port}",
                username="smoke",
                password="s3cret",
            ),
        )
        result = await visit(
            profile.to_camoufox_launch_options(),
            tmp_path / "proxy",
            "https://checkip.amazonaws.com",
        )

    connects = [e for e in proxy.entries if e.method == "CONNECT"]
    assert any(e.target == "checkip.amazonaws.com:443" and e.authorized for e in connects), (
        f"no authorized CONNECT through the proxy: {[(e.target, e.authorized) for e in connects]}"
    )
    # The CONNECT line carries the hostname, so DNS was answered by the proxy.
    targets = [e.target for e in connects if "checkip" in e.target]
    assert targets and not any(re.match(r"^\d+\.\d+\.\d+\.\d+:", t) for t in targets), (
        f"CONNECT targets were not hostnames: {targets}"
    )
    assert result["ok"], f"the page did not load through the proxy: {result}"
    print("PROXY_EXIT_BODY=", result["body"].strip()[:80])


@pytest.mark.asyncio
async def test_wrong_proxy_credentials_are_rejected_and_block_the_page(tmp_path):
    async with HttpConnectProxy(user="smoke", password="s3cret") as proxy:
        profile = Profile(
            name="win-badcreds",
            browser_settings=BrowserSettings(os="windows"),
            proxy=ProxyConfig(
                type=ProxyType.HTTP,
                server=f"127.0.0.1:{proxy.port}",
                username="smoke",
                password="WRONG",
            ),
        )
        result = await visit(
            profile.to_camoufox_launch_options(),
            tmp_path / "badcreds",
            "https://checkip.amazonaws.com",
            timeout=15000,
        )

    attempts = [e for e in proxy.entries if e.method == "CONNECT"]
    assert attempts, "the browser never attempted the proxy"
    assert not any(e.authorized for e in attempts), "the proxy accepted wrong credentials"
    assert not result["ok"], f"navigation unexpectedly succeeded: {result}"


@pytest.mark.asyncio
async def test_dead_proxy_blocks_navigation_without_fallback(tmp_path):
    """A proxy that is down must fail the page, never fall back to direct."""
    profile = Profile(
        name="win-dead",
        browser_settings=BrowserSettings(os="windows"),
        proxy=ProxyConfig(type=ProxyType.HTTP, server="127.0.0.1:9"),
    )
    result = await visit(
        profile.to_camoufox_launch_options(),
        tmp_path / "dead",
        "https://checkip.amazonaws.com",
        timeout=15000,
    )
    assert not result["ok"], f"a dead proxy fell back to a working connection: {result}"


@pytest.mark.asyncio
async def test_direct_control_connection_loads(tmp_path):
    """Control for the two tests above: this machine can reach the endpoint."""
    profile = Profile(name="win-direct", browser_settings=BrowserSettings(os="windows"))
    result = await visit(
        profile.to_camoufox_launch_options(), tmp_path / "direct", "https://checkip.amazonaws.com"
    )
    assert result["ok"], f"the control connection failed: {result}"


@pytest.mark.asyncio
async def test_socks5_without_credentials_tunnels(tmp_path):
    """A credential-free SOCKS5 proxy must work, and its DNS behaviour recorded."""
    async with Socks5Proxy() as proxy:
        profile = Profile(
            name="win-socks",
            browser_settings=BrowserSettings(os="windows"),
            proxy=ProxyConfig(type=ProxyType.SOCKS5, server=f"127.0.0.1:{proxy.port}"),
        )
        result = await visit(
            profile.to_camoufox_launch_options(),
            tmp_path / "socks",
            "https://checkip.amazonaws.com",
        )

    assert proxy.entries, "the browser never spoke SOCKS5"
    assert result["ok"], f"the SOCKS5 tunnel did not carry the page: {result}"
    # Camoufox sets network.proxy.socks_remote_dns=true, so the browser must send
    # the hostname (atyp 3) and let the proxy resolve it — no local DNS leak.
    atyp_by_target = {entry.target: entry.atyp for entry in proxy.entries}
    assert any(atyp == 3 for atyp in atyp_by_target.values()), (
        f"no domain-mode request; the browser resolved DNS locally: {atyp_by_target}"
    )
    # atyp 1 = IPv4 literal (local DNS), 3 = hostname (remote DNS).
    print("SOCKS5_REQUESTS=", [(e.atyp, e.target) for e in proxy.entries])


@pytest.mark.asyncio
async def test_webrtc_candidates_do_not_expose_private_addresses_with_proxy(tmp_path):
    async with HttpConnectProxy(user="smoke", password="s3cret") as proxy:
        profile = Profile(
            name="win-rtc-proxy",
            browser_settings=BrowserSettings(os="windows"),
            proxy=ProxyConfig(
                type=ProxyType.HTTP,
                server=f"127.0.0.1:{proxy.port}",
                username="smoke",
                password="s3cret",
            ),
        )
        candidates = await gather_ice(profile.to_camoufox_launch_options(), tmp_path / "rtc-proxy")

    leaked = [c for c in candidates if PRIVATE.search(c)]
    print("ICE_WITH_PROXY=", candidates)
    assert not leaked, f"private addresses in ICE candidates: {leaked}"


@pytest.mark.asyncio
async def test_webrtc_candidates_do_not_expose_private_addresses_without_proxy(tmp_path):
    profile = Profile(name="win-rtc-direct", browser_settings=BrowserSettings(os="windows"))
    candidates = await gather_ice(profile.to_camoufox_launch_options(), tmp_path / "rtc-direct")

    leaked = [c for c in candidates if PRIVATE.search(c)]
    print("ICE_NO_PROXY=", candidates)
    assert not leaked, f"private addresses in ICE candidates: {leaked}"
