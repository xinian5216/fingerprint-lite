"""Async SOCKS5 probe: domain handshake (remote DNS) then TLS, in-loop.

The earlier blocking-socket probe deadlocked the event loop it shared with the
server. This one never blocks: asyncio streams plus StreamWriter.start_tls.
"""

import asyncio
import ssl

from smoke.proxy_server import Socks5Proxy

TARGETS = ("checkip.amazonaws.com", "icanhazip.com")


async def probe(port: int, host: str) -> str:
    context = ssl.create_default_context()
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 10)
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    greeting = await asyncio.wait_for(reader.readexactly(2), 10)
    if greeting != b"\x05\x00":
        raise ConnectionError(f"greeting refused: {greeting.hex()}")

    request = b"\x05\x01\x00\x03" + bytes([len(host)]) + host.encode() + (443).to_bytes(2, "big")
    writer.write(request)
    await writer.drain()
    reply = await asyncio.wait_for(reader.readexactly(10), 10)
    if len(reply) != 10 or reply[1] != 0:
        raise ConnectionError(f"connect refused: {reply.hex()}")

    await writer.start_tls(context, server_hostname=host)
    writer.write(
        f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: probe\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()
    data = await asyncio.wait_for(reader.read(200), 15)
    writer.close()
    return (
        reply.hex() + " -> " + data.decode("latin1", errors="replace").replace("\r\n", " | ")[:120]
    )


async def main() -> None:
    async with Socks5Proxy() as socks:
        for host in TARGETS:
            try:
                print(f"inproc {host}: {await probe(socks.port, host)}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"inproc {host}: FAILED {type(exc).__name__}: {exc}", flush=True)
        print("inproc entries:", [(e.atyp, e.target) for e in socks.entries], flush=True)
        print("inproc raw:", socks.raw, flush=True)
        print("inproc errors:", socks.errors, flush=True)

    for host in TARGETS:
        try:
            print(f"standalone {host}: {await probe(8898, host)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"standalone {host}: FAILED {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
