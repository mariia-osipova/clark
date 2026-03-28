# Nacho — Backend, API & Infrastructure

You are assisting Nacho. His focus is API contracts, backend wiring, SQLite schemas and migrations, auth and session flow, integration hardening, and release branch stability.

## Files he owns
- `backend/app.py` — REST endpoints (catalog, auth, orders, chat)
- `backend/server.py` — ThreadingHTTPServer entry point and env loading
- `data/proxy_store.db` — SQLite database (users, sessions, orders, recurring plans)
- `requirements.txt`
- `.env.example`

## Current version: VERSION0 tasks
- [ ] Wire the Python server with env loading and API envelopes
- [ ] Create minimum SQLite schema: users, sessions (that later versions will extend)
- [ ] Expose `GET /api/v1/catalog` serving the normalized catalog snapshot
- [ ] Expose `POST /api/v1/chat` accepting history and returning assistant text
- [ ] Keep local boot simple: `python backend/server.py` should start the demo

## Version roadmap (Nacho)
| Version | Focus |
|---|---|
| V0 | Server wiring, env loading, API envelopes, minimum SQLite foundation, catalog + chat endpoints |
| V1 | Stabilize cart payload and API validation, keep totals consistent, cart-critical server tests |
| V2 | Persist preferences and order history in SQLite, stable chat context assembly |
| V3 | Stabilize clarification request/response contract, defensive validation, logging/request tracing |
| V4 | SQLite tables for recurring plans and plan items, config persistence, generation-to-order path |

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
