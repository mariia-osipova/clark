# Project Log

Reverse-chronological log of significant events. See [LOGGING.md](LOGGING.md) for how to add entries.

## 2026-03-28 — Nacho — VERSION4: session carts + recurring plan persistence + generate/accept flow

**Type:** feature

Added `session_carts`, `recurring_plans`, and `recurring_plan_items` tables to `backend/db.py` and `scripts/migrate_v4_session_carts_and_plans.py`. Implemented `GET/POST /api/v1/cart` and `POST /api/v1/cart/remove` for server-side cart state keyed by session_id. Implemented `GET/POST /api/v1/recurring-plan` for monthly config persistence (upsert on id='default'). Implemented `POST /api/v1/recurring-plan/generate`: loads plan + order history + catalog, calls `generate_monthly_basket_candidates()` from product_semantic_index, falls back to rule-based stub (priority items + frequency-ranked history) if Juan's function is not yet available. Implemented `POST /api/v1/recurring-plan/accept`: validates proposed cart against catalog via `_validate_order_cart()` and saves as a new order. Updated `docs/api.md`. 168 tests passing.

---

## 2026-03-28 — Jeremias — Permanent rule: no ad hoc agent behavior fixes

**Type:** decision

Recorded a permanent engineering rule for the shopping agent: never solve behavior bugs with phrase-specific heuristics, one-off guards, or other ad hoc logic. Future enforced behavior in `backend/chat_agent_agentic.py` must be expressed through LangGraph structure, graph state, tool-grounded runtime invariants, or similarly general mechanisms. Added this as a repo non-negotiable in `CLAUDE.md` and as a Jeremias-area convention in `team/JEREMIAS.md`.
## 2026-03-28 — Nacho — VERSION3: clarification contract + defensive validation + request logging

**Type:** feature

Added `_validate_clarification_response()`: validates pending_request_id and chosen_option_id (type, non-empty, strips whitespace), returns None for any malformed payload. Added `_validate_chat_body()`: normalizes message (string, stripped), filters invalid history items, filters cart items without product_id, validates clarification payload. Added structured request logging via Python `logging` module — chat requests log request_id, message preview, cart size, and clarification flag; errors log with exc_info. Added `_handle_orders_post` body validation (400 on non-list cart). 135 tests passing.

---

## 2026-03-28 — Juan — VERSION3 started: V2 merged, advancing to clarification popup + offers-aware ranking

**Type:** version

V2 is complete and merged to main. All V2 unite gate items pass. Advancing to VERSION3: clarification popup end-to-end + offers-aware cart reasoning. Critical path: Jeremias implements ambiguity detection and continuation → Juan defines clarification candidate sets and extends discount weight in ranking → Mariia renders option cards → Nacho validates clarification payload contract.

---

## 2026-03-28 — Juan — V2 bug fixes: cosine similarity, eval harness robustness

**Type:** decision

Fixed critical bug in `_cosine()` in `product_semantic_index.py`: was returning raw dot product instead of true cosine similarity, causing magnitude-biased rankings. Fixed by dividing by `norm_a * norm_b`. Fixed eval harness silent-pass bug: `_llm_judge()` now returns `passed=False` on API errors instead of silently passing. Added `min_cart_size` and `expected_min_quantity` fields to `Scenario` dataclass — `v1_exact_product` and `v1_quantity` now actually validate cart contents instead of passing unconditionally. Strict verdict parsing (`^PASS:|^FAIL:` regex) and product names in judge prompt also added. 27 tests passing.

---

## 2026-03-27 — Juan — V2 semantic search, find_alternatives, eval harness expansion

**Type:** feature

Upgraded `search()` in `product_semantic_index.py` to hybrid semantic + keyword retrieval using sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`); falls back to keyword filter when index is absent. Added `find_alternatives(query, catalog, category, top_k)` for stock-aware substitution with category-first, then cross-category fallback. Added module-level model and index caching. Expanded `llm_eval_harness.py` with V2 scenarios (`v2_broad_query`, `v2_out_of_stock_substitution`), LLM judge via gpt-4o-mini for v2-tagged scenarios, and `--no-llm-judge` flag in runner for CI. 28 tests passing.

---

## 2026-03-27 — Nacho — VERSION2: preferences persistence + chat context assembly
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
