"""API-level Windows checks against a running camoufox-pm instance.

Verifies: health + web UI, profile CRUD light path, launch/close lifecycle,
same-instance double launch, browser directory isolation, fingerprint pinning
after launch, proxy checks (working / dead / SOCKS-with-credentials preflight),
and proxy-password encryption at rest when CPM_SECRET_KEY is set.

Usage:
    uv run --no-sync python smoke/api_checks.py [base_url] [db_path]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT.parent / "evidence"

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/api/v1"
DB_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "profiles.db"

PROXY_SERVER = "127.0.0.1:8899"
PROXY_USER = "smoke"
PROXY_PASSWORD = "s3cret"

results: list[dict[str, Any]] = []


def profile_directory_for(db_path: Path, profile_id: str) -> Path:
    """Return a profile directory under the data root containing ``db_path``."""
    return db_path.parent / "profiles" / f"profile_{profile_id}"


def check(name: str, ok: bool, detail: Any = None) -> bool:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    printed = ""
    if detail is not None:
        printed = json.dumps(detail, ensure_ascii=False)
        if len(printed) > 400:
            printed = printed[:400] + "...(truncated; full detail in api_checks.json)"
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {printed}" if printed else ""))
    return bool(ok)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    origin = BASE.split("/api/", 1)[0]
    with httpx.Client(timeout=180.0) as client:
        # 1. Health and web UI
        health = client.get(f"{origin}/health")
        check("health endpoint", health.status_code == 200, health.json())
        root = client.get(origin + "/")
        check(
            "web UI served (index.html)",
            root.status_code == 200 and "html" in root.headers.get("content-type", ""),
            f"status={root.status_code} bytes={len(root.content)}",
        )

        # 2. Create two profiles
        stamp = datetime.now().strftime("%H%M%S")
        created = {}
        for key, name in (("A", f"win-smoke-A-{stamp}"), ("B", f"win-smoke-B-{stamp}")):
            response = client.post(
                f"{BASE}/profiles",
                json={"name": name, "browser_settings": {"os": "windows"}},
            )
            assert response.status_code == 201, response.text
            created[key] = response.json()["id"]
        check("create profiles A and B", len(created) == 2, created)

        # 3. Launch A, then launch A again (same instance)
        launch_a = client.post(f"{BASE}/profiles/{created['A']}/launch", json={"headless": True})
        check(
            "launch A (headless)",
            launch_a.status_code == 200 and launch_a.json()["status"] == "launched",
            launch_a.json(),
        )
        again = client.post(f"{BASE}/profiles/{created['A']}/launch", json={"headless": True})
        check(
            "same-profile double launch is refused",
            again.status_code == 200 and again.json()["status"] == "already_running",
            again.json(),
        )

        # 4. Launch B; both active
        launch_b = client.post(f"{BASE}/profiles/{created['B']}/launch", json={"headless": True})
        active = client.get(f"{BASE}/browsers/active").json()
        check(
            "both profiles run simultaneously",
            launch_b.json()["status"] == "launched" and active["count"] == 2,
            active,
        )

        # 5. Directory isolation
        dirs = {}
        for key, pid in created.items():
            path = profile_directory_for(DB_PATH, pid)
            files = list(path.rglob("*")) if path.exists() else []
            dirs[key] = {"path": str(path), "exists": path.exists(), "entries": len(files)}
        check(
            "separate non-empty profile directories",
            dirs["A"]["entries"] > 0
            and dirs["B"]["entries"] > 0
            and dirs["A"]["path"] != dirs["B"]["path"],
            dirs,
        )

        # 6. Close A; B still running; close all
        closed = client.post(f"{BASE}/profiles/{created['A']}/close")
        after_close = client.get(f"{BASE}/browsers/active").json()
        check(
            "close A leaves only B",
            closed.json()["status"] == "closed" and after_close["count"] == 1,
            after_close,
        )
        close_all = client.post(f"{BASE}/browsers/close-all")
        check(
            "close-all closes the rest",
            close_all.json()["closed_count"] == 1,
            close_all.json(),
        )

        # 7. The pin exists after the first launch
        profile_a = client.get(f"{BASE}/profiles/{created['A']}").json()
        fingerprint = profile_a.get("fingerprint") or {}
        check(
            "fingerprint pinned after launch",
            bool(fingerprint) and fingerprint.get("property_count", 0) > 0,
            fingerprint,
        )

        # 8. Working proxy check (external test proxy must be running)
        proxy_settings = {
            "type": "http",
            "server": PROXY_SERVER,
            "username": PROXY_USER,
            "password": PROXY_PASSWORD,
        }
        profile_c_response = client.post(
            f"{BASE}/profiles",
            json={
                "name": f"win-smoke-C-proxy-{stamp}",
                "browser_settings": {"os": "windows"},
                "proxy_config": proxy_settings,
            },
        )
        assert profile_c_response.status_code == 201, profile_c_response.text
        profile_c = profile_c_response.json()
        proxy_check = client.post(f"{BASE}/profiles/{profile_c['id']}/check-proxy").json()
        check(
            "proxy check through test proxy succeeds",
            proxy_check.get("reachable") is True
            and bool(proxy_check.get("location", {}).get("ip")),
            proxy_check,
        )
        profile_c_after = client.get(f"{BASE}/profiles/{profile_c['id']}").json()
        check(
            "proxy check persisted on the profile",
            profile_c_after.get("proxy_check") is not None,
            profile_c_after.get("proxy_check", {}).get("checked_at"),
        )

        # 9. Dead proxy must report unreachable
        profile_d_response = client.post(
            f"{BASE}/profiles",
            json={
                "name": f"win-smoke-D-dead-{stamp}",
                "browser_settings": {"os": "windows"},
                "proxy_config": {"type": "http", "server": "127.0.0.1:9"},
            },
        )
        assert profile_d_response.status_code == 201, profile_d_response.text
        profile_d = profile_d_response.json()
        dead_check = client.post(f"{BASE}/profiles/{profile_d['id']}/check-proxy").json()
        check(
            "dead proxy reports unreachable",
            dead_check.get("reachable") is False and bool(dead_check.get("error")),
            dead_check,
        )

        # 10. SOCKS with credentials: the documented preflight error
        socks_check = client.post(
            f"{BASE}/proxy/check",
            json={
                "proxy_config": {
                    "type": "socks5",
                    "server": "127.0.0.1:1",
                    "username": "u",
                    "password": "p",
                },
                "browser_settings": {"os": "windows"},
            },
        ).json()
        findings = socks_check.get("findings", [])
        check(
            "SOCKS+credentials preflight error present",
            any(f.get("level") == "error" and f.get("field") == "proxy" for f in findings),
            findings,
        )

        # 11. Proxy password encrypted at rest
        if DB_PATH.exists():
            connection = sqlite3.connect(DB_PATH)
            try:
                row = connection.execute(
                    "SELECT proxy_config FROM profiles WHERE id = ?", (profile_c["id"],)
                ).fetchone()
            finally:
                connection.close()
            stored = json.loads(row[0]) if row and row[0] else {}
            password = stored.get("password", "")
            check(
                "proxy password encrypted at rest (enc: + Fernet token)",
                password.startswith("enc:gAAAA") and password != PROXY_PASSWORD,
                {"stored_prefix": password[:16], "user": stored.get("username")},
            )
        else:
            check("proxy password encrypted at rest", False, f"db not found at {DB_PATH}")

    passed = sum(1 for item in results if item["ok"])
    summary = {"total": len(results), "passed": passed, "failed": len(results) - passed}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "api_checks.json").write_text(
        json.dumps({"summary": summary, "checks": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"SUMMARY={json.dumps(summary)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
