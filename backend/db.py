"""
SQLite helpers — schema init and connection factory.
Owner: Nacho

Tables (V0):
    users    — registered users
    sessions — session tokens linked to users
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
    """)
    conn.commit()
    conn.close()
