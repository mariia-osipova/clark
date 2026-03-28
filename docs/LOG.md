# Project Log

Reverse-chronological log of significant events. See [LOGGING.md](LOGGING.md) for how to add entries.

## 2026-03-27 — Jeremias — Switched agent to LangGraph

**Type:** architecture

Replaced the raw OpenAI agentic loop in `chat_agent_agentic.py` with a LangGraph `StateGraph`. Graph: `START → agent → tools → agent → … → END`. Added `search_products` tool (calls `product_semantic_index.search()`), updated `set_cart` and `request_clarification` as LangChain `@tool` functions. Added `langgraph`, `langchain-openai`, `langchain-core` to `requirements.txt`. CLAUDE.md non-negotiable updated to reflect LangGraph as the agent framework. Public `handle_chat()` signature and return shape unchanged. 30 tests passing.

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
