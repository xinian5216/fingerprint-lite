"""Probe raw TLS reachability of candidate targets, direct and through the proxy."""

import base64
import socket
import ssl

# Local test-proxy credentials only; the header is computed instead of embedded.
_AUTH = base64.b64encode(b"smoke:s3cret").decode()

TARGETS = [
    ("api.ipify.org", 443),
    ("checkip.amazonaws.com", 443),
    ("icanhazip.com", 443),
    ("example.com", 443),
    ("github.com", 443),
]

HTTP_GET = b"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: probe\r\nConnection: close\r\n\r\n"


def tls_get(host: str, port: int, sock: socket.socket | None = None) -> str:
    context = ssl.create_default_context()
    if sock is None:
        sock = socket.create_connection((host, port), timeout=15)
    tls = context.wrap_socket(sock, server_hostname=host)
    tls.settimeout(15)
    tls.sendall(HTTP_GET.replace(b"{host}", host.encode()))
    data = tls.recv(200).decode("latin1", errors="replace").replace("\r\n", " | ")
    tls.close()
    return data[:160]


def via_http_proxy(host: str, port: int) -> str:
    sock = socket.create_connection(("127.0.0.1", 8899), timeout=15)
    request = (
        f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Proxy-Authorization: Basic {_AUTH}\r\n\r\n"
    )
    sock.sendall(request.encode())
    sock.settimeout(15)
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError("proxy closed before response")
        head += chunk
    first_line = head.split(b"\r\n", 1)[0].decode("latin1")
    if "200" not in first_line:
        raise ConnectionError(f"proxy refused: {first_line}")
    return f"proxy: {first_line} -> " + tls_get(host, port, sock=sock)


def main() -> None:
    for host, port in TARGETS:
        try:
            result = tls_get(host, port)
            print(f"direct {host}: {result}")
        except Exception as exc:  # noqa: BLE001
            print(f"direct {host}: FAILED {type(exc).__name__}: {exc}")
    for host, port in TARGETS:
        try:
            result = via_http_proxy(host, port)
            print(f"proxy  {host}: {result}")
        except Exception as exc:  # noqa: BLE001
            print(f"proxy  {host}: FAILED {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
