"""Reproduce the profile-creation failure with a proxy, printing the raw response."""

import json
import sys

import httpx

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1"
payload = {
    "name": "win-smoke-C-repro",
    "browser_settings": {"os": "windows"},
    "proxy_config": {
        "type": "http",
        "server": "127.0.0.1:8899",
        "username": "smoke",
        "password": "s3cret",
    },
}
response = httpx.post(f"{base}/profiles", json=payload, timeout=60)
print("status:", response.status_code)
print("headers:", dict(response.headers))
print("body:", json.dumps(response.json(), indent=2, ensure_ascii=False)[:2000])
