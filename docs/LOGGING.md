# Logging Guide

All significant events must be appended to [LOG.md](LOG.md). This keeps the team synchronized when working concurrently with separate agent instances.

## What to log
- Version milestones (starting a version, completing the unite gate)
- API contract changes (new endpoints, changed payloads, breaking changes)
- SQLite schema changes (new tables, migrations)
- Scraper runs and catalog updates (timestamp, item count, source)
- Major design decisions (why a specific approach was chosen)
- Blockers and how they were resolved
- Eval results (scenario name, pass/fail, regression notes)

## What NOT to log
- Routine code edits (git history handles that)
- Typo fixes or formatting changes
- Work in progress that hasn't landed yet

## Log entry format

Append to `docs/LOG.md`. Use this format:

```
## YYYY-MM-DD HH:MM — [Name] — Short title

**Type:** version | api | schema | catalog | decision | blocker | eval

Body: one to three sentences explaining what happened and why it matters.
Include specific details: endpoint names, table names, item counts, scenario names.
```

## Example entries

```
## 2026-03-27 14:30 — Nacho — Added users and sessions tables

**Type:** schema

Created initial SQLite schema with `users` (id, email, password_hash, created_at)
and `sessions` (token, user_id, expires_at). Migration script at `scripts/migrate_v0.py`.

## 2026-03-27 15:00 — Juan — First catalog snapshot

**Type:** catalog

Scraped 847 products from Carrefour. Output written to `data/catalog_snapshot.json`.
Fields: id, name, brand, package_size, price, image_url, available_quantity, category, discount_pct.

## 2026-03-27 16:00 — Jeremias — POST /api/v1/chat contract updated

**Type:** api

Added `cart` field to chat request body. See docs/api.md for full schema.
```

## Tips
- Log when you start a significant task, not just when it finishes — helps teammates know what's in flight.
- If you are an agent and you complete a unite gate item, log it.
- Keep entries short. One paragraph max.
