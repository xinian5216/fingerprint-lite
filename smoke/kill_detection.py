"""Kill the browser process externally and verify the manager notices.

This is the Windows equivalent of the user closing the window (or the browser
crashing): the close must be noticed by the session monitor, and the lease
released, without a manager restart.
"""

from __future__ import annotations

import subprocess
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1"


def main() -> int:
    with httpx.Client(timeout=180.0) as client:
        created = client.post(
            f"{BASE}/profiles",
            json={
                "name": f"win-smoke-kill-{int(time.time())}",
                "browser_settings": {"os": "windows"},
            },
        ).json()
        profile_id = created["id"]
        launched = client.post(
            f"{BASE}/profiles/{profile_id}/launch", json={"headless": True}
        ).json()
        print(f"[INFO] launched: {launched['status']} (process_id={launched.get('process_id')})")
        time.sleep(5)

        before = client.get(f"{BASE}/browsers/active").json()
        print(f"[INFO] active before kill: {before['count']}")

        killed = subprocess.run(
            ["taskkill", "/IM", "camoufox.exe", "/T", "/F"],
            capture_output=True,
            text=True,
        )
        print(f"[INFO] taskkill rc={killed.returncode} out={killed.stdout.strip()[:120]}")

        cleaned_after = None
        active = {"count": "unknown"}
        for second in range(60):
            active = client.get(f"{BASE}/browsers/active").json()
            if active["count"] == 0:
                cleaned_after = second
                break
            time.sleep(1)

        assert cleaned_after is not None, (
            f"the manager still reports a running browser after the kill: {active}"
        )
        print(f"[PASS] session cleaned up {cleaned_after}s after taskkill")

        # The lease must be free again: a launch must work without any unlock.
        relaunched = client.post(f"{BASE}/profiles/{profile_id}/launch", json={"headless": True})
        assert relaunched.status_code == 200 and relaunched.json()["status"] == "launched", (
            f"lease was not released after the kill: {relaunched.status_code} {relaunched.text}"
        )
        print("[PASS] profile relaunched after the kill (lease released)")
        client.post(f"{BASE}/profiles/{profile_id}/close")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
