"""
Migration V4: session_carts, recurring_plans, recurring_plan_items tables.

Safe to run multiple times (IF NOT EXISTS). Run from repo root:
    python scripts/migrate_v4_session_carts_and_plans.py
"""

import os
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
            CREATE TABLE IF NOT EXISTS session_carts (
                session_id TEXT    NOT NULL,
                product_id TEXT    NOT NULL,
                quantity   INTEGER NOT NULL DEFAULT 1,
                added_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (session_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS recurring_plans (
                id                  TEXT PRIMARY KEY,
                household_size      INTEGER NOT NULL DEFAULT 1,
                monthly_budget      REAL,
                priority_items      TEXT    NOT NULL DEFAULT '[]',
                preferred_brands    TEXT    NOT NULL DEFAULT '{}',
                strict_brand        INTEGER NOT NULL DEFAULT 0,
                excluded_categories TEXT    NOT NULL DEFAULT '[]',
                notes               TEXT    NOT NULL DEFAULT '',
                created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recurring_plan_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id    TEXT    NOT NULL REFERENCES recurring_plans(id) ON DELETE CASCADE,
                product_id TEXT    NOT NULL,
                quantity   INTEGER NOT NULL DEFAULT 1,
                tag        TEXT    NOT NULL DEFAULT 'must_have'
            );
        """)
        conn.commit()
        print("V4 migration complete: session_carts, recurring_plans, recurring_plan_items created.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
