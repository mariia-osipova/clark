# Nacho — Backend, API & Infrastructure

You are assisting Nacho. His focus is API contracts, backend wiring, SQLite schemas and migrations, auth and session flow, integration hardening, and release branch stability.

## Files he owns
- `backend/app.py` — REST endpoints (catalog, auth, orders, chat)
- `backend/server.py` — ThreadingHTTPServer entry point and env loading
- `data/proxy_store.db` — SQLite database (users, sessions, orders, recurring plans)
- `requirements.txt`
- `.env.example`

## Current version: VERSION4 active

## V3 ✅
- [x] Clarification request/response contract stabilized (`pending_request_id` + `chosen_option_id`)
- [x] `_validate_clarification_response()` and `_validate_chat_body()` defensive validation
- [x] Structured request logging with request IDs and exc_info

## Current focus (V4)
- [ ] `session_carts` SQLite table + `add_to_cart(session_id, product_id, quantity)` / `remove_from_cart` / `get_cart` endpoints so the agent never tracks the full cart list in context
- [ ] `recurring_plans` and `recurring_plan_items` SQLite tables with full CRUD
- [ ] `POST /api/v1/recurring-plan/generate` → calls Juan's `generate_monthly_basket_candidates()`, returns proposed cart
- [ ] `POST /api/v1/recurring-plan/accept` → saves proposed basket as a new order
- [ ] Migration scripts in `scripts/` for all new tables; document in [docs/LOG.md](../docs/LOG.md)

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
