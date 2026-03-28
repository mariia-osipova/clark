"""
Migration V4b: pending_clarifications table.

Safe to run multiple times (IF NOT EXISTS). Run from repo root:
    python scripts/migrate_v4_pending_clarifications.py
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db import get_db_path


def migrate() -> None:
    db_path = get_db_path()
    if not db_path.exists():
        print(f"DB not found at {db_path} — run the server first to initialise it.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pending_clarifications (
                session_id         TEXT NOT NULL,
                pending_request_id TEXT NOT NULL,
                original_query     TEXT,
                options_json       TEXT,
                created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at        TEXT,
                PRIMARY KEY (session_id, pending_request_id)
            );
        """)
        conn.commit()
        print("V4b migration complete: pending_clarifications created.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
