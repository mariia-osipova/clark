"""
SQLite helpers — schema init and connection factory.
Owner: Nacho

Tables:
    users                — registered users
    sessions             — session tokens linked to users
    orders               — placed orders with cart snapshot and total
    preferences          — user preference blob (key='default' until auth lands)
    session_carts        — server-side per-session cart items
    recurring_plans      — monthly shopping configuration (id='default' until auth lands)
    recurring_plan_items — pinned items belonging to a recurring plan
"""

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get_db_path() -> Path:
    return Path(os.environ.get("DB_PATH", ROOT / "data" / "proxy_store.db"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token      TEXT    NOT NULL UNIQUE,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id         TEXT PRIMARY KEY,
                cart_json  TEXT NOT NULL,
                total      REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS preferences (
                key        TEXT PRIMARY KEY,
                prefs_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

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
    finally:
        conn.close()
