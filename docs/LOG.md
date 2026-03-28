# Project Log

Reverse-chronological log of significant events. See [LOGGING.md](LOGGING.md) for how to add entries.

---

## 2026-03-27 — Nacho — SQLite minimum schema

**Type:** schema

Created `backend/db.py` with `init_db()` and `get_db()`. Tables: `users` (id, username, email, password_hash, created_at) and `sessions` (id, user_id FK, token, created_at, expires_at). `init_db()` called on server startup in `server.py`. Auth endpoints remain stubbed (V1). `get_db` imported in `app.py` for use in V1 handlers.

---

## 2026-03-27 — Jeremias — Repo skeleton created

**Type:** version

Created VERSION0 repo skeleton: CLAUDE.md, team files (JEREMIAS.md, JUAN.md, MARIIA.md, NACHO.md), docs/, frontend stubs, backend stubs, data/, scripts/, requirements.txt, .env.example. App renamed to supershop. Ready for team to start parallel work on VERSION0 tasks.
