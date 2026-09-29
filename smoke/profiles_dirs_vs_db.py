"""Compare profile directories on disk with profile rows in the database."""

import sqlite3
import sys
from pathlib import Path

db = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parent.parent / "data" / "profiles.db"
)
storage = db.parent / "profiles"
ids = {row[0] for row in sqlite3.connect(db).execute("SELECT id FROM profiles")}
dirs = {path.name for path in storage.iterdir() if path.is_dir()}
orphans = sorted(dirs - {f"profile_{profile_id}" for profile_id in ids})
print(f"db_rows={len(ids)} dirs={len(dirs)} orphans={len(orphans)}")
for name in orphans:
    print(f"  orphan: {name}")
