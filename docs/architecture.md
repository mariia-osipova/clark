# Architecture

## Stack
| Layer | Technology |
|---|---|
| Frontend | Vanilla HTML + CSS + JS (no frameworks) |
| Backend | Python 3 `ThreadingHTTPServer` / `SimpleHTTPRequestHandler` |
| Database | SQLite (`data/proxy_store.db`) |
| Catalog data | JSON snapshot (`data/catalog_snapshot.json`) |
| Semantic index | JSON (`data/product_semantic_index.json`) |
| AI agent | OpenAI API via `backend/chat_agent_agentic.py` |

## Request flow

```
Browser
  └─ GET /api/v1/catalog   → backend/app.py → data/catalog_snapshot.json
  └─ POST /api/v1/chat     → backend/app.py → chat_agent_agentic.py → OpenAI
  └─ POST /api/v1/auth/*   → backend/app.py → SQLite users/sessions
  └─ GET/POST /api/v1/orders → backend/app.py → SQLite orders
```

## Frontend state machine (`frontend/app.js`)
- Tabs: Catalog | Cart | Chat | (V4: Monthly Buys)
- Persistent state in `localStorage`: cart items, session token, chat history
- Cart total computed client-side, confirmed by server on each chat response

## Backend modules
| File | Responsibility |
|---|---|
| `server.py` | Entry point, env loading, `ThreadingHTTPServer` setup |
| `app.py` | Route dispatch, request parsing, response envelope |
| `chat_agent.py` | Thin compatibility shim (legacy) |
| `chat_agent_agentic.py` | Agentic shopping flow, tool calls, cart mutations |
| `product_semantic_index.py` | Semantic retrieval and ranking over catalog snapshot |
| `llm_eval_harness.py` | LLM judge eval suite definitions |

## Data directory
| File | Description |
|---|---|
| `catalog_snapshot.json` | Normalized product catalog from Carrefour scrape |
| `product_semantic_index.json` | Pre-built semantic index for retrieval |
| `proxy_store.db` | SQLite — users, sessions, orders, recurring plans |

## API envelope (all responses)
```json
{
  "ok": true,
  "data": { },
  "error": null,
  "request_id": "uuid-v4"
}
```

## Environment variables
See `.env.example` for full list. Required at minimum:
- `OPENAI_API_KEY`
- `PORT` (default: 8000)
- `DB_PATH` (default: `data/proxy_store.db`)
