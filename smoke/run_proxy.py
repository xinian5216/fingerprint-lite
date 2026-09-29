"""Run the HTTP test proxy on a fixed port until interrupted.

Usage: python -m smoke.run_proxy 8899 smoke s3cret

The module form matters: the script imports ``smoke.proxy_server``, so the
repository root has to be on ``sys.path`` — running the file by path puts the
script's own directory there instead.
"""

import asyncio
import sys

from smoke.proxy_server import HttpConnectProxy


async def main() -> None:
    port = int(sys.argv[1])
    user = sys.argv[2] if len(sys.argv) > 2 else None
    password = sys.argv[3] if len(sys.argv) > 3 else None
    proxy = HttpConnectProxy(user, password)
    await proxy.start(port)
    print(f"test proxy listening on 127.0.0.1:{port}", flush=True)
    await proxy.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
