# HACKATHON BUILD PLAN

## Entrega Ya - current execution plan

This plan follows the real codebase shape that already exists today, but it is trimmed for a hackathon. Every item below should either improve the live demo, reduce demo risk, or unlock the next visible milestone. Anything that does not materially help the demo is intentionally left out.

## Actual stack to keep during the hackathon

### Frontend
- Vanilla HTML in `frontend/index.html`
- Vanilla CSS in `frontend/styles.css`
- Vanilla JavaScript state machine in `frontend/app.js`
- UI already supports tabs, cart rendering, chat thread, clarification modal, and `localStorage` state

### Backend
- Python 3 stdlib server with `ThreadingHTTPServer` and `SimpleHTTPRequestHandler`
- Main API surface in `backend/app.py` and `server.py`
- REST endpoints already exist for catalog, auth, orders, and chat
- Structured JSON envelope plus request IDs already match the current server style

### Data and retrieval
- Catalog source: Carrefour public catalog, proxied and normalized
- Local snapshot: `data/catalog_snapshot.json`
- Semantic retrieval index: `data/product_semantic_index.json`
- SQLite database: `data/proxy_store.db` for users, sessions, orders, and future recurring plans

### AI and quality
- Agentic shopping flow centered on `backend/chat_agent_agentic.py`
- `backend/chat_agent.py` can remain only as a thin compatibility layer if the UI/server still expects it
- Semantic ranking in `backend/product_semantic_index.py`
- Quality loop: `pytest` plus LLM judge eval suite in `backend/llm_eval_harness.py` and `scripts/run_llm_judge_eval.py`

## Team focus throughout

| Person | Standing emphasis across the current project phases |
|---|---|
| Jeremias | Agent behavior, prompt strategy, multi-step cart assembly, clarification logic, automation logic |
| Juan | Catalog scraping, product normalization, ranking, semantic search, offers reasoning, evaluation harness and regression tracking |
| Mariia | Layout system, chat and cart UX, clarification popup, monthly configuration flows, visual polish, demo quality |
| Nacho | API contracts, backend wiring, SQLite schemas and migrations, auth and session flow, integration hardening, release branch stability |

## Current target

**Clarification popup plus offers-aware cart reasoning**

**Expected completion time:** 5 to 7 hours

**Goal:** avoid wrong guesses and optimize better than search

### Concrete deliverable
When the assistant is unsure between several materially different options, it does not guess. It asks the user via a popup/modal. At the same time, it starts reasoning over discounts and offers so it can build a better cart, not just the first matching cart.

### Technical scope
- Define a clarification payload that the backend returns and the frontend can resume with exactly.
- Implement ambiguity thresholds so brand, size, or category conflicts trigger user confirmation.
- Wire the popup end-to-end: assistant proposes options, UI shows them, user picks one, backend resumes the same pending request set.
- Use offer and discount fields in ranking so cart construction prefers strong value when it does not violate user intent.
- Display prices, discount markers, and comparison-friendly option cards inside the popup.

### Jeremias
- Implement clarification generation and continuation logic.
- Make the agent produce structured option sets instead of free-form uncertainty.
- Blend user intent with offers logic without becoming too aggressive.

### Juan
- Extend ranking to consider discount percentage and list-price deltas.
- Define the candidate set used for clarifications.
- Expand evals for ambiguous cola, size conflicts, and close-brand choices.

### Mariia
- Design the popup/modal interaction so it feels fast and obvious, not disruptive.
- Show option cards with image, brand, size, price, and discount in a scannable way.
- Polish the transition between chat reply, modal selection, and updated cart.

### Nacho
- Stabilize the clarification request/response contract in the API.
- Add defensive validation so malformed clarification payloads cannot break the app.
- Keep logging and request tracing strong enough to debug unclear failures during demo prep.

### Unite gate
- Ambiguous requests such as `gaseosa cola 1.5L` open a clarification popup instead of silently guessing.
- User selection resumes the exact pending task and updates the cart correctly.
- Offer-aware choices are visible and explainable in the UI.
- The clarification and offers scenarios pass in the eval suite.

## Next phase

**Full automation with a monthly buys tab**

**Expected completion time:** 7 to 10 hours

**Goal:** turn the assistant into a recurring shopping system

### Concrete deliverable
A user can configure a recurring monthly shopping profile and generate a monthly cart automatically from saved preferences, order history, must-have items, budget constraints, and current offers. The main outcome is not background scheduling; the main outcome is one-click monthly cart generation with stored configuration.

### Technical scope
- Add a new monthly-buys tab with persistent configuration.
- Store recurring shopping inputs in SQLite: household size, monthly budget, priority items, preferred brands, strict-brand flag, excluded categories, and free-text notes.
- Build a monthly cart generation flow that merges the recurring config with prior orders and current catalog availability.
- Let the agent optimize the recurring basket for stock and offers while preserving must-have items and user preferences.
- Preview the generated basket before save, including what was repeated, swapped, skipped, or newly suggested.

### Jeremias
- Design the monthly planning prompt and automation logic.
- Make the agent combine fixed requirements with historical buying patterns.
- Make recurring generation reproducible enough that the team can review and trust the output live.

### Juan
- Use order history and offers data to rank monthly basket candidates.
- Improve bundle-level reasoning so the monthly cart is coherent, not just item-by-item greedy.
- Create eval cases for monthly restock, budget pressure, and missing essentials.

### Mariia
- Design the monthly config tab and make it feel like a product, not an admin form.
- Show a before/after preview comparing the proposed monthly cart with the last saved plan or last order.
- Make approval, override, and re-run flows extremely clear.

### Nacho
- Create the SQLite tables and API endpoints for recurring plans and plan items.
- Guarantee config persistence, retrieval, and safe generation under real app sessions.
- Own the final integration path from recurring config to generated cart to saved order.

### Unite gate
- User can save a recurring monthly profile and load it later.
- Monthly generation builds a full proposed cart using config plus history plus current stock/offers.
- The generated cart is reviewable and saveable as a normal order.
- Automation scenarios are demoable without hidden manual setup.

## Cross-version unite checklist
- At the end of every version, merge to one demo branch and run the smallest useful verification set.
- From `version2` onward, run the LLM judge suite and track only the scenarios that protect the live demo.
- Do not start the next version until the current version has one reproducible happy-path demo.
- Keep the external stack stable: vanilla frontend, Python stdlib backend, SQLite, JSON snapshot/index, OpenAI-based agent layer.
- Prefer additive changes over rewrites; the fastest path is to evolve the current code shape, not replace it.

Built around the current proxy-store structure and intentionally optimized for hackathon demo quality.
