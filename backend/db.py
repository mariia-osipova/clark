"""
SQLite helpers — schema init and connection factory.
Owner: Nacho

Tables:
    users                    — registered users
    sessions                 — session tokens linked to users
    orders                   — placed orders with cart snapshot and total
    preferences              — user preference blob (key='default' until auth lands)
    session_carts            — server-side per-session cart items (authoritative)
    recurring_plans          — monthly shopping configuration (id='default' until auth lands)
    recurring_plan_items     — pinned items belonging to a recurring plan
    pending_clarifications   — issued clarification requests, resolved on answer
"""

import json
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
        # WAL mode: readers don't block writers, writers don't block readers.
        # Required for ThreadingHTTPServer where concurrent cart + chat writes are normal.
        conn.execute("PRAGMA journal_mode=WAL")
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
    finally:
        conn.close()


# ─── Session cart helpers ──────────────────────────────────────────────────────

def get_session_cart(session_id: str, catalog: list) -> list:
    """Return fully hydrated, catalog-validated cart items for session_id.
    Out-of-stock and unknown products are silently dropped (same rules as _validate_order_cart).
    """
    catalog_map = {p["id"]: p for p in catalog}
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT product_id, quantity FROM session_carts WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    items = []
    for r in rows:
        pid = r["product_id"]
        product = catalog_map.get(pid)
        if not product or product.get("available_quantity", 0) == 0:
            continue
        items.append({
            "product_id": pid,
            "name": product.get("name", ""),
            "brand": product.get("brand", ""),
            "package_size": product.get("package_size", ""),
            "price": product.get("price", 0.0),
            "quantity": r["quantity"],
            "image_url": product.get("image_url", ""),
        })
    return items


def upsert_session_cart_item(session_id: str, product_id: str, quantity: int) -> None:
    """Upsert a session cart row inside a BEGIN EXCLUSIVE transaction."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            """INSERT INTO session_carts (session_id, product_id, quantity)
               VALUES (?, ?, ?)
               ON CONFLICT(session_id, product_id) DO UPDATE SET
                   quantity = excluded.quantity""",
            (session_id, product_id, quantity),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_session_cart_item(session_id: str, product_id: str) -> None:
    """Delete a session cart row inside a BEGIN EXCLUSIVE transaction."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            "DELETE FROM session_carts WHERE session_id = ? AND product_id = ?",
            (session_id, product_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def clear_session_cart(session_id: str) -> None:
    """Delete all items for a session (called after checkout)."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("DELETE FROM session_carts WHERE session_id = ?", (session_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def place_order(session_id: str, order_id: str, cart: list, total: float) -> None:
    """Insert order and clear session cart atomically in a single transaction.
    If either operation fails the whole thing rolls back — no orphaned orders
    or carts that survive checkout.
    """
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            "INSERT INTO orders (id, cart_json, total) VALUES (?, ?, ?)",
            (order_id, json.dumps(cart), total),
        )
        conn.execute(
            "DELETE FROM session_carts WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Pending clarification helpers ────────────────────────────────────────────

def save_pending_clarification(
    session_id: str,
    pending_request_id: str,
    original_query: str,
    options: list,
) -> None:
    """Persist an issued clarification so it can be validated on reply."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO pending_clarifications
                   (session_id, pending_request_id, original_query, options_json)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id, pending_request_id) DO NOTHING""",
            (session_id, pending_request_id, original_query, json.dumps(options)),
        )
        conn.commit()
    finally:
        conn.close()


def resolve_pending_clarification(session_id: str, pending_request_id: str) -> bool:
    """Mark a clarification as resolved atomically.
    Returns True only if the row existed and was previously unresolved.
    Uses a single UPDATE with WHERE resolved_at IS NULL to avoid check-then-act race.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            """UPDATE pending_clarifications
               SET resolved_at = datetime('now')
               WHERE session_id = ? AND pending_request_id = ? AND resolved_at IS NULL""",
            (session_id, pending_request_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()
