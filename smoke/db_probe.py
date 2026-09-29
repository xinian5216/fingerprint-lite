"""Read-only probe of the verification database (profiles table)."""

import json
import sqlite3
import sys
from pathlib import Path

db = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parent.parent / "data" / "profiles.db"
)
connection = sqlite3.connect(db)
connection.row_factory = sqlite3.Row
try:
    rows = connection.execute(
        "SELECT id, name, fingerprint, proxy_config, storage_path, last_used FROM profiles ORDER BY created_at"
    ).fetchall()
    print(f"db={db} rows={len(rows)}")
    for row in rows:
        fingerprint = row["fingerprint"]
        proxy = row["proxy_config"]
        try:
            fingerprint_len = len(json.loads(fingerprint)) if fingerprint else 0
        except Exception:  # noqa: BLE001
            fingerprint_len = -1
        try:
            proxy_keys = list(json.loads(proxy)) if proxy else []
        except Exception:  # noqa: BLE001
            proxy_keys = ["<unparsable>"]
        print(
            f"- {row['id']} {row['name']!r} fingerprint_keys={fingerprint_len} "
            f"proxy_keys={proxy_keys} last_used={row['last_used']}"
        )
finally:
    connection.close()
