"""Run the SOCKS5 test proxy on a fixed port, logging every connection.

    python -u -m smoke.run_socks 8898

The log file (next to this script's parent, evidence/socks_server.log) records
each connection's first bytes and the parsed request, so a browser that never
reaches the proxy is distinguishable from one whose handshake fails.
"""

import asyncio
import sys
from pathlib import Path

from smoke.proxy_server import Socks5Proxy

LOG = Path(__file__).resolve().parent.parent.parent / "evidence" / "socks_server.log"


async def log_loop(proxy: Socks5Proxy) -> None:
    seen_raw = 0
    seen_entries = 0
    while True:
        await asyncio.sleep(1)
        with LOG.open("a", encoding="utf-8") as handle:
            while seen_raw < len(proxy.raw):
                handle.write(f"conn #{seen_raw + 1} greeting={proxy.raw[seen_raw]}\n")
                seen_raw += 1
            while seen_entries < len(proxy.entries):
                entry = proxy.entries[seen_entries]
                handle.write(f"conn #{seen_entries + 1} atyp={entry.atyp} target={entry.target}\n")
                seen_entries += 1
            while proxy.errors:
                handle.write(f"error: {proxy.errors.pop(0)}\n")
            handle.flush()


async def main() -> None:
    port = int(sys.argv[1])
    proxy = Socks5Proxy()
    await proxy.start(port)
    LOG.write_text(f"socks test proxy listening on 127.0.0.1:{port}\n", encoding="utf-8")
    print(f"test socks proxy listening on 127.0.0.1:{port}", flush=True)
    await asyncio.gather(proxy.serve_forever(), log_loop(proxy))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
