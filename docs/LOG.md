# Project Log

Reverse-chronological log of significant events. See [LOGGING.md](LOGGING.md) for how to add entries.

## 2026-03-28 — Jeremias — Permanent rule: no ad hoc agent behavior fixes

**Type:** decision

Recorded a permanent engineering rule for the shopping agent: never solve behavior bugs with phrase-specific heuristics, one-off guards, or other ad hoc logic. Future enforced behavior in `backend/chat_agent_agentic.py` must be expressed through LangGraph structure, graph state, tool-grounded runtime invariants, or similarly general mechanisms. Added this as a repo non-negotiable in `CLAUDE.md` and as a Jeremias-area convention in `team/JEREMIAS.md`.

---

## 2026-03-28 — Jeremias — Agent weakness fixes landed and chat contract documented

**Type:** api

Hardened `chat_agent_agentic.py` and `app.py` after the agent weakness review: clarification tool calls now halt immediately and carry a grounded `pending_request_id`, cart validation reports dropped items, history trimming is char-budget based, and the graph/catalog are cached between requests with timeout + recursion guards. `docs/api.md` now documents `clarification_response`, `clarification`, and `missing_items`, and `_serve_static()` blocks path traversal attempts.

---

## 2026-03-27 — Nacho — Preferences persistence + chat context assembly

**Type:** feature

Added `preferences` table to SQLite schema together with the preferences migration script. Implemented `GET /api/v1/preferences` and `PUT /api/v1/preferences` endpoints. Added `_assemble_chat_context()` in app.py: loads last 3 orders and saved preferences from SQLite, builds a context string passed to `handle_chat()` as a `context` kwarg. Added `context` param to `handle_chat()` signature (chat_agent_agentic.py) — injected as a SystemMessage before history. Added integration and unit tests to tests/test_api.py. Updated docs/api.md.

---

## 2026-03-27 — Jeremias — Agent query decomposition, substitution, and missing tracking

**Type:** feature

Updated `chat_agent_agentic.py` to decompose recipe/goal prompts into per-ingredient `search_products` calls, apply nearest-match substitution when exact isn't found, and reply with a structured ✅/🔄/❌ summary. Added `report_missing` tool (records unfindable ingredients) and `missing_items: list[str]` to `AgentState` and the `handle_chat()` return dict. `handle_chat()` now returns `{ reply, cart, clarification, missing_items }`. 82 tests passing.

---

## 2026-03-27 — Jeremias — Switched agent to LangGraph

**Type:** architecture

Replaced the raw OpenAI agentic loop in `chat_agent_agentic.py` with a LangGraph `StateGraph`. Graph: `START → agent → tools → agent → … → END`. Added `search_products` tool (calls `product_semantic_index.search()`), updated `set_cart` and `request_clarification` as LangChain `@tool` functions. Added `langgraph`, `langchain-openai`, `langchain-core` to `requirements.txt`. CLAUDE.md non-negotiable updated to reflect LangGraph as the agent framework. Public `handle_chat()` signature and return shape unchanged. 30 tests passing.

---

## 2026-03-27 — Nacho — Orders persistence + cart validation

**Type:** feature

Added `orders` table to SQLite schema together with the orders migration script. Implemented `_validate_order_cart()` in app.py: drops unknown/out-of-stock items, enforces catalog prices over client-supplied values. `POST /api/v1/orders` now validates cart and persists to SQLite. `GET /api/v1/orders` reads order history from SQLite. Added tests/test_api.py (envelope, cart validation, total computation, catalog and orders integration tests). Updated .gitignore.

---

## 2026-03-27 — Jeremias — Initial unite gate passed, moving to cart mutation work

**Type:** version

Merged jere, Juan, and nacho branches into main. All 26 tests pass. Unite gate confirmed: catalog endpoint returns 50 normalized products, frontend shell renders, chat endpoint responds. Pre-commit hook installed — pytest must pass before every commit. CLAUDE.md updated to the next target.

---

## 2026-03-27 — Nacho — SQLite minimum schema

**Type:** schema

Created `backend/db.py` with `init_db()` and `get_db()`. Tables: `users` (id, username, email, password_hash, created_at) and `sessions` (id, user_id FK, token, created_at, expires_at). `init_db()` called on server startup in `server.py`. Auth endpoints remain stubbed. `get_db` imported in `app.py` for use in later handlers.

---

## 2026-03-27 — Juan — Product ranking: brand/size matching and unit normalisation

**Type:** decision

Enhanced `rank_candidates()` in `backend/product_semantic_index.py` with three new scoring signals: exact brand match (+5, accent-insensitive via `_normalize_text`), exact package-size match (+5, unit-normalised via `_normalize_size` so "1L" == "1000ml" == "1 litro"), and OOS filter (mirrors the one already in `search()`). Created `tests/test_product_ranking.py` with 15 unit tests covering helpers and both public functions. Eval scenarios in `llm_eval_harness.py` annotated — `expected_product_ids` to be filled after first real scrape.

---

## 2026-03-27 — Juan — Catalog scraper and embedding index implemented

**Type:** catalog

Created `scripts/scrape_catalog.py`: paginates Carrefour Argentina VTEX API, normalizes products to the supershop schema (id, name, brand, package_size, price, list_price, discount_pct, offer_label, image_url, available_quantity, category), and writes `data/catalog_snapshot.json`. Updated `build_index()` in `backend/product_semantic_index.py` to generate per-product embeddings via `text-embedding-3-small` (batched, 512/call) and persist them in `data/product_semantic_index.json`. Run with `python scripts/scrape_catalog.py`; use `--skip-index` to skip the embedding step, `--max-products N` for quick test runs.

---

## 2026-03-27 — Jeremias — Repo skeleton created

**Type:** version

Created the initial repo skeleton: CLAUDE.md, team files (JEREMIAS.md, JUAN.md, MARIIA.md, NACHO.md), docs/, frontend stubs, backend stubs, data/, scripts/, requirements.txt, .env.example. App renamed to supershop. Ready for parallel work.

---

## 2026-03-28 01:34 — Jeremias — Clarification flow now enforced for ambiguous search results

**Type:** blocker

Root cause: clarification existed only as prompt guidance plus manual tool handling, so a broad query like `leche` could still flow straight from `search_products` to `set_cart` without ever asking the user to choose. Added a server-side ambiguity gate in `backend/chat_agent_agentic.py` that auto-returns clarification options for generic multi-match searches, plus regression tests covering the heuristic and the real LangGraph path. `tests/test_chat_agent.py` and `tests/test_api.py` both pass after the fix.

---

## 2026-03-28 01:41 — Jeremias — Clarification trigger now covers option-browsing phrasing

**Type:** blocker

Follow-up fix for a missed real prompt: phrases like `dame opciones para galletitas` were not treated as broad ambiguous shopping requests because the heuristic only matched short generic queries. Normalized the query before ambiguity detection so filler words like `dame opciones para` still produce a `clarification` payload and popup, and added regressions for that exact wording in `tests/test_chat_agent.py`.

---

## 2026-03-28 01:43 — Jeremias — Replaced clarification heuristic with graph-level unresolved-choice handling

**Type:** decision

Removed the ad hoc token-based ambiguity detection from `backend/chat_agent_agentic.py`. The graph now records viable search results as structured selection candidates and only converts them into a `clarification` payload when the agent tries to end the turn without either mutating the cart or explicitly calling `request_clarification`, which keeps the flow agentic while still guaranteeing the popup for unresolved option lists. Full suite after the change: `123 passed, 1 skipped`.
