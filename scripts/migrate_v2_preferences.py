"""
Migration V2 — add preferences table.
Owner: Nacho

Run once on any existing V1 database:
    python scripts/migrate_v2_preferences.py

Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
"""

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.server import load_env

load_env()

db_path = Path(os.environ.get("DB_PATH", ROOT / "data" / "proxy_store.db"))

if not db_path.exists():
    print(f"Database not found at {db_path} — nothing to migrate.")
    sys.exit(0)

conn = sqlite3.connect(db_path)
conn.execute("""
    CREATE TABLE IF NOT EXISTS preferences (
        key        TEXT PRIMARY KEY,
        prefs_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
""")
conn.commit()
conn.close()

print(f"Migration V2 applied to {db_path}")
