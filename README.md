# supershop

AI-powered shopping assistant. Vanilla JS frontend, Python stdlib backend, LangGraph agent, OpenAI embeddings.

## Requirements

- Python 3.10+
- An OpenAI API key (`text-embedding-3-small` + `gpt-4o-mini`)
- Node.js (only if running the Playwright tests)

## Setup

### 1. Clone and install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy the example and fill in your key:

```bash
cp .env.example .env
# edit .env and set OPENAI_API_KEY=sk-...
```

Available variables:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** Used for embeddings and the chat agent. |
| `PORT` | `8000` | Port the server listens on. |
| `HOST` | `0.0.0.0` | Bind address. |
| `DB_PATH` | `data/proxy_store.db` | SQLite database path. |

### 3. Set up the catalog

The catalog is a JSON snapshot of Carrefour AR products. It is already committed at `data/catalog_snapshot.json` (2 548 products). You do **not** need to re-scrape it to run the app.

If you ever need to refresh it:

```bash
python scripts/scrape_catalog.py
```

> **Note:** scraping takes several minutes and requires network access to carrefour.com.ar.

### 4. Build the semantic index

The semantic index (`data/product_semantic_index.json`) is also committed and ready to use. To rebuild it from scratch (costs a few cents of OpenAI API credits):

```bash
python -c "
from backend.product_semantic_index import build_index
import json
catalog = json.load(open('data/catalog_snapshot.json'))
build_index(catalog)
print('done')
"
```

### 5. Seed the demo database

Creates two past orders, user preferences, and a recurring monthly plan — needed for the checkout nudge and canasta mensual features:

```bash
python scripts/seed_demo.py
```

Safe to re-run at any time (idempotent).

### 6. Start the server

```bash
python backend/server.py
```

Open [http://localhost:8000/chat.html](http://localhost:8000/chat.html).

---

## Demo flows

The chat page opens with "Hola, Martín 👋" and five quick-tap buttons that exercise every major feature:

| Chip | What it tests |
|---|---|
| 📦 **Cargar desayuno** | Load a saved cart profile (milk, yogurt, bread, butter) |
| 🗂 **Cargar despensa** | Load a saved cart profile (rice, pasta, oil, eggs, yerba) |
| 💾 **Guardar carrito** | Save the current cart under a name |
| ✅ **Confirmar pedido** | Checkout — triggers forgotten-item nudge if the last order had items missing from the current cart |
| 📅 **Canasta mensual** | Generate a proposed monthly basket from the recurring plan |

---

## Project layout

```
backend/        Python server + LangGraph agent
  server.py         Entry point (ThreadingHTTPServer)
  app.py            HTTP request handler / routing
  chat_agent_agentic.py  LangGraph graph definition
  product_semantic_index.py  OpenAI vector search
  db.py             SQLite helpers
  user_profile.py   Profile read/write helpers

frontend/       Vanilla HTML/CSS/JS
  chat.html         Main chat page
  chat-app.js       Chat, cart, catalog, demo chips
  chat-style.css    All styles for the chat page
  index.html        Landing page

data/
  catalog_snapshot.json       2 548 products (Carrefour AR)
  product_semantic_index.json OpenAI text-embedding-3-small, 1 536-dim
  proxy_store.db              SQLite — orders, carts, preferences, plans
  user_profile.json           Demo user profile (Martín García)

scripts/
  seed_demo.py                Seed DB with demo orders + recurring plan
  scrape_catalog.py           Re-scrape Carrefour AR catalog
  migrate_v*.py               Schema migration scripts

tests/                        pytest suite (228 tests)
```

---

## Running tests

```bash
pytest
```

Tests that require a real OpenAI key are skipped automatically when `OPENAI_API_KEY` is unset or set to a test placeholder (`sk-test-...`).

---

## Architecture notes

- **Search:** pure vector search using `text-embedding-3-small` (1 536-dim) + numpy cosine similarity. Similarity floor 0.45, retries at 0.30 on empty results. Falls back to keyword search.
- **Agent:** LangGraph state machine — `classify_turn → resolve_items → apply_cart → summarize`. Checkout and profile save/load are deterministic short-circuit nodes (no extra LLM calls).
- **State:** session carts stored in SQLite `session_carts` table, keyed by `X-Session-Token` header. Browser `localStorage` mirrors the cart for rendering.
- **No framework deps on the frontend** — vanilla JS, no build step required.
