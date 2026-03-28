# Nacho — Backend, API & Infrastructure

You are assisting Nacho. His focus is API contracts, backend wiring, SQLite schemas and migrations, auth and session flow, integration hardening, and release branch stability.

## Files he owns
- `backend/app.py` — REST endpoints (catalog, auth, orders, chat)
- `backend/server.py` — ThreadingHTTPServer entry point and env loading
- `data/proxy_store.db` — SQLite database (users, sessions, orders, recurring plans)
- `requirements.txt`
- `.env.example`

## Current version: VERSION3 ✅ → VERSION4 active (Robustness Rebuild)

## V3 complete
- [x] Stabilize the clarification request/response contract in the API
- [x] Add defensive validation so malformed clarification payloads cannot break the app
- [x] Keep logging and request tracing strong enough to debug unclear demo failures

## V4 focus — Robustness Rebuild

### Immediate bug fixes (do these first)
- [ ] **Bug #5 HIGH** — `app.py:623-625`: `_generate_basket_stub()` fallback produces a completely different algorithm than `generate_monthly_basket_candidates()`. Remove the stub. Import `generate_monthly_basket_candidates` at module load and fail fast if missing — no silent fallback.
- [ ] **Bug #3 HIGH** — `frontend/app.js:191-209`: checkout sends `state.cart` (localStorage) instead of reading the server session cart. Fix `_handle_orders_post()` to use `get_session_cart(session_id)` from DB, not the request body `cart`.
- [ ] **Bug #4 HIGH** — `frontend/app.js:93,125`: `addToCart()` and `updateCartQty()` only update localStorage — never sync to server. Fix both to call `POST /api/v1/cart` (upsert) and rollback local state on failure.

### Phase 1 — Cart authority unification

**`frontend/app.js` changes:**
- `addToCart()`: call `POST /api/v1/cart` (upsert) after local add. On failure: rollback the optimistic local state.
- `updateCartQty()`: call `POST /api/v1/cart` with updated quantity on every change.
- `removeFromCart()`: keep server call, add retry + explicit rollback if it fails (not fire-and-forget `.catch(console.warn)`).
- On page load: call `GET /api/v1/cart?session_id=...` and replace localStorage with server state — server is authoritative.
- When server returns `cart` in any chat response: replace localStorage with that value entirely (not merge).

**`app.py` changes:**
- When `_validate_cart()` drops OOS/unknown items, include `dropped_items: list[str]` (product names) in the chat response envelope so frontend can remove them from localStorage.
- `_handle_orders_post()`: read cart from `get_session_cart(session_id)` (DB), not from request body. This makes checkout use the server cart, not stale localStorage.

**`db.py` changes:**
- Add `get_session_cart(session_id: str, catalog: list) -> list[dict]` helper — shared by chat and checkout.
- Wrap all `session_carts` write operations (`INSERT OR REPLACE`) in `BEGIN EXCLUSIVE` transaction to prevent race condition from two concurrent writes (two browser tabs, or chat + REST cart add simultaneously).

### Phase 2 — Clarification persistence & validation

**Problem:** `pending_request_id` is issued by the server but never stored server-side. Any `pending_request_id` string from the client is accepted without verification.

**`db.py` changes:**
- Add `pending_clarifications` table:
  ```sql
  CREATE TABLE IF NOT EXISTS pending_clarifications (
      session_id TEXT NOT NULL,
      pending_request_id TEXT NOT NULL,
      original_query TEXT,
      options_json TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      resolved_at TEXT,
      PRIMARY KEY (session_id, pending_request_id)
  );
  ```
- Add migration script `scripts/migrate_v4_pending_clarifications.py`
- Add helpers: `save_pending_clarification(session_id, pending_request_id, original_query, options)` and `resolve_pending_clarification(session_id, pending_request_id) -> bool`

**`app.py` changes:**
- When agent returns a `clarification` dict, call `save_pending_clarification()` before returning response.
- Before dispatching `clarification_response` to Juan's `handle_chat()`, validate: `resolve_pending_clarification(session_id, pending_request_id)` returns True (exists AND `resolved_at IS NULL`). If not: return 400 with `"error": "stale or unknown clarification request"`.

### Phase 3 — Monthly basket API cleanup
- [ ] Remove `_generate_basket_stub()` from `app.py` entirely
- [ ] Both `POST /api/v1/recurring-plan/generate` and chat `action=generate_monthly_basket` call the same `generate_monthly_basket_candidates()` from `product_semantic_index`
- [ ] Add `budget_exceeded: bool` to the `/recurring-plan/generate` response envelope so frontend can optionally surface a warning
- [ ] Write a test in `tests/test_api.py` asserting both entrypoints return structurally identical `proposed_cart` for the same inputs

### Phase 4 — `app.py` structural cleanup
Break up the monolithic `_handle_chat()` in `app.py`:
- `_normalize_chat_request(body: dict) -> dict` — validates and coerces request fields
- `_dispatch_chat(request: dict) -> dict` — calls `handle_chat()` from `chat_agent_agentic`, returns raw result
- `_build_chat_response(result: dict, session_id: str) -> dict` — hydrates server cart via `get_session_cart()`, assembles final JSON envelope

### Phase 5 — API docs parity
- [ ] Remove or clearly mark `POST /api/v1/auth/register` and `POST /api/v1/auth/login` from `docs/api.md` as `NOT IMPLEMENTED`
- [ ] Add `pending_clarifications` lifecycle section: when issued, how to reply, what happens on stale ID
- [ ] Document `dropped_items` field in chat response
- [ ] Document cart authority model: "server `session_carts` table is authoritative; `localStorage` is a cache that must be replaced on every server response"
- [ ] Update `POST /api/v1/recurring-plan/generate` docs to reflect `budget_exceeded` field

## How to help Nacho
- When he asks to add or change an endpoint, update `backend/app.py` and [docs/api.md](../docs/api.md) together.
- When changing SQLite schema, write a migration script in `scripts/` and document in [docs/LOG.md](../docs/LOG.md).
- API responses must always use the structured envelope: `{ "ok": bool, "data": ..., "error": str|null, "request_id": str }`.
- Never expose raw exceptions to the API response — catch and wrap.
- Log all schema changes and breaking API changes in [docs/LOG.md](../docs/LOG.md).

## Key conventions
- Server entry point: `python backend/server.py`
- Default port: `8000` (configurable via `PORT` env var)
- Auth: session token in header `X-Session-Token`
- All endpoints under `/api/v1/`
- CORS must be open for local dev (frontend on same origin or localhost)
- SQLite file: `data/proxy_store.db` — never commit it, it's in `.gitignore`

## API envelope (standard)
```json
{
  "ok": true,
  "data": {},
  "error": null,
  "request_id": "uuid"
}
```
