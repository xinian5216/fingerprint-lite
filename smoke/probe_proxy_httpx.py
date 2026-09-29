"""Probe the test proxies with httpx (no browser) to isolate proxy vs Firefox."""

import asyncio

import httpx

from smoke.proxy_server import HttpConnectProxy, Socks5Proxy


async def main() -> None:
    async with HttpConnectProxy(user="smoke", password="s3cret") as proxy:
        for attempt in range(2):
            try:
                response = httpx.get(
                    "https://api.ipify.org",
                    proxy=f"http://smoke:s3cret@127.0.0.1:{proxy.port}",
                    timeout=20,
                )
                print(f"http attempt {attempt}: {response.status_code} {response.text[:40]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"http attempt {attempt}: FAILED {type(exc).__name__}: {exc}")
        print("http entries:", [(e.method, e.target, e.authorized) for e in proxy.entries])
        print("http errors:", proxy.errors)

        try:
            httpx.get(
                "https://api.ipify.org",
                proxy=f"http://127.0.0.1:{proxy.port}",
                timeout=10,
            )
            print("http no-creds: unexpectedly succeeded")
        except Exception as exc:  # noqa: BLE001
            print(f"http no-creds: refused as expected -> {type(exc).__name__}: {str(exc)[:80]}")

    async with Socks5Proxy() as socks:
        for attempt in range(2):
            try:
                response = httpx.get(
                    "https://api.ipify.org",
                    proxy=f"socks5://127.0.0.1:{socks.port}",
                    timeout=20,
                )
                print(f"socks attempt {attempt}: {response.status_code} {response.text[:40]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"socks attempt {attempt}: FAILED {type(exc).__name__}: {exc}")
        print("socks entries:", [(e.atyp, e.target) for e in socks.entries])
        print("socks errors:", socks.errors)


if __name__ == "__main__":
    asyncio.run(main())
