"""Mutual exclusion between two manager instances sharing one database.

Usage:
    uv run --no-sync python smoke/api_two_instances.py <url1> <url2> [profile_id]

Without a profile_id, a fresh profile is created on instance 1 first.

Asserts: instance 1 holds a live lease; instance 2 gets 409; after instance 1
closes the browser, instance 2 can launch the same profile; it then closes it.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

import httpx


def launch(client: httpx.Client, base: str, profile_id: str) -> httpx.Response:
    return client.post(f"{base}/profiles/{profile_id}/launch", json={"headless": True})


def main() -> int:
    url1, url2 = sys.argv[1], sys.argv[2]
    profile_id = sys.argv[3] if len(sys.argv) > 3 else None
    with httpx.Client(timeout=180.0) as client:
        if not profile_id:
            created = client.post(
                f"{url1}/profiles",
                json={
                    "name": f"win-smoke-two-instances-{datetime.now().strftime('%H%M%S')}",
                    "browser_settings": {"os": "windows"},
                },
            )
            assert created.status_code == 201, created.text
            profile_id = created.json()["id"]
            print(f"[INFO] created profile {profile_id} on instance 1")

        first = launch(client, url1, profile_id)
        assert first.status_code == 200 and first.json()["status"] == "launched", first.text
        print(f"[PASS] instance 1 holds the profile: {first.json()['status']}")

        conflict = launch(client, url2, profile_id)
        assert conflict.status_code == 409, (
            f"expected 409 from the second instance, got {conflict.status_code}: {conflict.text}"
        )
        print(f"[PASS] instance 2 refused with 409: {conflict.json().get('detail')}")

        client.post(f"{url1}/profiles/{profile_id}/close")
        takeover = None
        for _ in range(30):
            candidate = launch(client, url2, profile_id)
            if candidate.status_code == 200:
                takeover = candidate
                break
            time.sleep(1)
        assert takeover is not None, "instance 2 could not take the profile after close"
        print(f"[PASS] instance 2 launched after release: {takeover.json()['status']}")

        closed = client.post(f"{url2}/profiles/{profile_id}/close")
        assert closed.json()["status"] == "closed", closed.text
        print("[PASS] instance 2 closed the browser and released the lease")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
