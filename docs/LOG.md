# Project Log

Reverse-chronological log of significant events. See [LOGGING.md](LOGGING.md) for how to add entries.

## 2026-03-27 — Nacho — VERSION1 backend: orders persistence + cart validation

**Type:** feature

Added `orders` table to SQLite schema (backend/db.py, scripts/migrate_v1_orders.py). Implemented `_validate_order_cart()` in app.py: drops unknown/out-of-stock items, enforces catalog prices over client-supplied values. `POST /api/v1/orders` now validates cart and persists to SQLite. `GET /api/v1/orders` reads order history from SQLite. Added tests/test_api.py (envelope, cart validation, total computation, catalog and orders integration tests). Updated .gitignore.

---

## 2026-03-27 — Jeremias — VERSION0 unite gate passed, starting VERSION1

**Type:** version

Merged jere, Juan, and nacho branches into main. All 26 tests pass. VERSION0 unite gate confirmed: catalog endpoint returns 50 normalized products, frontend shell renders, chat endpoint responds. Pre-commit hook installed — pytest must pass before every commit. CLAUDE.md updated to VERSION1 target.

---

## 2026-03-27 — Nacho — SQLite minimum schema

**Type:** schema

Created `backend/db.py` with `init_db()` and `get_db()`. Tables: `users` (id, username, email, password_hash, created_at) and `sessions` (id, user_id FK, token, created_at, expires_at). `init_db()` called on server startup in `server.py`. Auth endpoints remain stubbed (V1). `get_db` imported in `app.py` for use in V1 handlers.

---

## 2026-03-27 — Juan — Catalog scraper and embedding index implemented

**Type:** catalog

Created `scripts/scrape_catalog.py`: paginates Carrefour Argentina VTEX API, normalizes products to the supershop schema (id, name, brand, package_size, price, list_price, discount_pct, offer_label, image_url, available_quantity, category), and writes `data/catalog_snapshot.json`. Updated `build_index()` in `backend/product_semantic_index.py` to generate per-product embeddings via `text-embedding-3-small` (batched, 512/call) and persist them in `data/product_semantic_index.json`. Run with `python scripts/scrape_catalog.py`; use `--skip-index` to skip the embedding step, `--max-products N` for quick test runs.

---

## 2026-03-27 — Jeremias — Repo skeleton created

**Type:** version

Created VERSION0 repo skeleton: CLAUDE.md, team files (JEREMIAS.md, JUAN.md, MARIIA.md, NACHO.md), docs/, frontend stubs, backend stubs, data/, scripts/, requirements.txt, .env.example. App renamed to supershop. Ready for team to start parallel work on VERSION0 tasks.
