"""Local test proxies for Windows verification. Stdlib only; no repo changes.

HTTP proxy: CONNECT tunnelling with optional Basic auth, logging each request
(method, target, whether credentials were presented and accepted).
SOCKS5 proxy: no-auth CONNECT, logging whether the client sent a hostname
(remote DNS) or a literal address (the client resolved DNS itself).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import struct
import time
from dataclasses import dataclass


@dataclass
class HttpEntry:
    when: float
    method: str
    target: str
    authorized: bool


@dataclass
class SocksEntry:
    when: float
    cmd: int
    atyp: int  # 1 = IPv4, 3 = domain, 4 = IPv6
    target: str


class HttpConnectProxy:
    """A minimal HTTP proxy that supports CONNECT, with optional Basic auth."""

    def __init__(self, user: str | None = None, password: str | None = None) -> None:
        self.user = user
        self.password = password
        self.entries: list[HttpEntry] = []
        self.errors: list[str] = []
        self.port: int | None = None
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self, port: int = 0) -> int:
        """Start listening; returns the bound port."""
        self._server = await asyncio.start_server(self._spawn, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    def _spawn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        """Close the listener and cancel any tunnels still running."""
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def __aenter__(self) -> HttpConnectProxy:
        await self.start(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _expected_auth(self) -> str:
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        return f"Basic {token}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), 20)
            if not request_line:
                return
            parts = request_line.decode("latin1").strip().split()
            if len(parts) < 3:
                return
            method, target = parts[0], parts[1]
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), 20)
                if line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = line.decode("latin1").partition(":")
                headers[key.strip().lower()] = value.strip()

            authorized = True
            if self.user is not None:
                authorized = headers.get("proxy-authorization") == self._expected_auth()
            self.entries.append(HttpEntry(time.time(), method, target, authorized))

            if not authorized:
                writer.write(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="smoke"\r\n'
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            if method != "CONNECT":
                writer.write(b"HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return

            host, _, port_text = target.rpartition(":")
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port_text)), 20
            )
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await self._pump(reader, remote_writer, writer, remote_reader)
        except Exception as exc:  # noqa: BLE001 - recorded for diagnostics
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    async def _pump(
        client_reader: asyncio.StreamReader,
        server_writer: asyncio.StreamWriter,
        client_writer: asyncio.StreamWriter,
        server_reader: asyncio.StreamReader,
    ) -> None:
        async def one_way(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            except Exception:  # noqa: BLE001
                pass
            finally:
                with contextlib.suppress(Exception):
                    writer.close()

        await asyncio.gather(
            one_way(client_reader, server_writer), one_way(server_reader, client_writer)
        )


class Socks5Proxy:
    """A minimal SOCKS5 CONNECT proxy with no authentication."""

    def __init__(self) -> None:
        self.entries: list[SocksEntry] = []
        self.errors: list[str] = []
        self.raw: list[str] = []
        self.port: int | None = None
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self, port: int = 0) -> int:
        self._server = await asyncio.start_server(self._spawn, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    def _spawn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def __aenter__(self) -> Socks5Proxy:
        await self.start(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            greeting = await asyncio.wait_for(reader.readexactly(2), 20)
            self.raw.append(greeting.hex())
            version, method_count = greeting[0], greeting[1]
            methods = await asyncio.wait_for(reader.readexactly(method_count), 20)
            if version != 5 or 0x00 not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                return
            writer.write(b"\x05\x00")
            await writer.drain()

            header = await asyncio.wait_for(reader.readexactly(4), 20)
            version, cmd, _reserved, atyp = header
            if version != 5 or cmd != 1:
                writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
                await writer.drain()
                return

            if atyp == 1:
                raw = await asyncio.wait_for(reader.readexactly(4), 20)
                host = ".".join(str(b) for b in raw)
            elif atyp == 3:
                length = (await asyncio.wait_for(reader.readexactly(1), 20))[0]
                raw = await asyncio.wait_for(reader.readexactly(length), 20)
                # RFC 1928: a domain is ASCII (IDN arrives as punycode). The idna
                # codec rejects errors="replace", which is what broke this handler.
                host = raw.decode("ascii")
            elif atyp == 4:
                raw = await asyncio.wait_for(reader.readexactly(16), 20)
                host = ":".join(f"{raw[i]:02x}{raw[i + 1]:02x}" for i in range(0, 16, 2))
            else:
                writer.write(b"\x05\x08\x00\x01" + b"\x00" * 6)
                await writer.drain()
                return

            port = struct.unpack(">H", await asyncio.wait_for(reader.readexactly(2), 20))[0]
            self.entries.append(SocksEntry(time.time(), cmd, atyp, f"{host}:{port}"))

            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), 20
            )
            writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
            await writer.drain()

            async def one_way(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
                try:
                    while True:
                        data = await src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                        await dst.drain()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    with contextlib.suppress(Exception):
                        dst.close()

            await asyncio.gather(one_way(reader, remote_writer), one_way(remote_reader, writer))
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            with contextlib.suppress(Exception):
                writer.close()
