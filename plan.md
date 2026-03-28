# HACKATHON BUILD PLAN

## Entrega Ya - current execution plan

This plan follows the real codebase shape that already exists today, but it is trimmed for a hackathon. Every item below should either improve the live demo, reduce demo risk, or unlock the next visible milestone. Anything that does not materially help the demo is intentionally left out.

## Current status

| Version | Status | Branch merged to main |
|---|---|---|
| VERSION0 | ✅ Complete | Yes |
| VERSION1 | ✅ Complete | Yes |
| VERSION2 | ✅ Complete | Yes |
| VERSION3 | 🔜 Active | — |
| VERSION4 | ⬜ Upcoming | — |

**Active work:** VERSION3 — clarification popup + offers-aware ranking.

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

## VERSION0 ✅

**Catalog scrape, basic shell, simple talking agent**

**Expected completion time:** 4 to 6 hours

**Goal:** prove the stack and demo the basic product shape

### Concrete deliverable
A user can open the website, browse a scraped catalog, see a clean layout with chat plus cart, and send a message to a basic assistant that responds coherently even if it does not yet mutate the cart.

### Technical scope
- Build a repeatable catalog ingestion flow that fetches Carrefour data and writes a normalized local snapshot.
- Expose a minimal `GET /api/v1/catalog` that serves the normalized snapshot with product IDs, names, brand, package size, price, image URL, and available quantity.
- Stand up the frontend shell: top navigation, catalog grid, chat panel, cart placeholder, and empty states.
- Create a minimal `POST /api/v1/chat` that accepts chat history and returns assistant text, but does not yet promise reliable cart mutation.
- Keep persistence intentionally light: `localStorage` for front-end state, JSON snapshot for catalog, and only the minimum SQLite foundation that helps later versions.

### Jeremias
- Wrap a simple chat backend around the OpenAI API.
- Define the initial prompt style and reply tone.
- Make the assistant aware of the catalog context at a high level.

### Juan
- Implement the scraper/normalizer for catalog items.
- Standardize product fields so later versions do not need to redo ingestion.
- Add a first-pass query filter by product name/brand.

### Mariia
- Design the first visual system: layout, typography, spacing, and card language.
- Create the basic chat shell, product grid, and cart placeholder.
- Define empty, loading, and error states so the prototype already feels intentional.

### Nacho
- Wire the Python server, env loading, and API envelopes.
- Create only the minimum SQLite/data foundation the later versions will need.
- Keep local boot simple enough that the team can restart the demo quickly.

### Unite gate
- ✅ Catalog endpoint returns usable normalized products from a real snapshot.
- ✅ Website renders catalog, chat shell, and cart placeholder without broken layout.
- ✅ Chat endpoint returns non-empty responses reliably.
- ✅ The team can boot the project and show this version live without hidden setup.

## VERSION1 ✅

**Agent can add specific products to the cart**

**Expected completion time:** 5 to 7 hours

**Goal:** move from assistant demo to shopping assistant

### Concrete deliverable
The user can ask for a specific product such as a brand, size, or quantity, and the system can resolve the request to a concrete in-stock SKU and add it to the cart with the correct quantity.

### Technical scope
- Define the first real shopping contract in `POST /api/v1/chat`: request carries message, history, and cart; response returns reply plus authoritative cart.
- Implement exact product retrieval with brand/package-size matching and quantity parsing.
- Validate all cart items server-side before they reach the UI.
- Render the cart as a first-class surface: totals, quantities, add/remove buttons, and visual feedback after a successful add.
- Persist cart state in `localStorage` so refresh does not break the demo flow.

### Jeremias
- Implement the first real add-to-cart loop using the agentic path and strict cart-setting tools.
- Handle user phrasing like product name + size + quantity.
- Make the reply name the product that was actually added.

### Juan
- Build the product ranking logic for exact and near-exact matches.
- Filter out unavailable items before selection.
- Add initial tests for package-size and brand-sensitive matching.

### Mariia
- Turn the cart into a polished, readable panel with quantity controls.
- Add micro-feedback after cart mutations so users trust the action happened.
- Refine product cards so users can compare results visually.

### Nacho
- Stabilize the cart payload and API validation rules.
- Keep totals and item shapes consistent between backend and frontend.
- Add only the server tests needed to keep demo-critical cart behavior from breaking.

### Unite gate
- ✅ Queries such as `quiero leche entera 1L` and `agrega 2 yogures` result in concrete cart mutations.
- ✅ The cart survives refresh and shows consistent totals.
- ✅ Malformed cart items are rejected before UI rendering.
- ✅ The add-specific-product flow is covered by automated tests.

## VERSION2 ✅

**Complex queries, recipes, and stock-aware alternatives**

**Expected completion time:** 6 to 8 hours

**Goal:** make the assistant useful beyond exact search

### Concrete deliverable
The user can ask for a recipe, a meal goal, or a broader shopping intent, and the assistant can build a multi-item cart. If a needed item is not in stock, it must either choose a sensible alternative or explicitly explain what is missing.

### Technical scope
- Move from single-item selection to multi-request planning: recipe goals become several product requests.
- Add semantic retrieval so product discovery still works when the wording is broad or indirect.
- Implement stock-aware substitution logic with reply summaries that separate added, substituted, and missing items.
- Start passing richer context to the assistant: current cart, user preferences, and recent order history.
- Make the UI show cart changes clearly after a complex turn.

### Jeremias
- Implement query decomposition for recipe and goal prompts.
- Control how the agent decides between add, replace, and explain-only behavior.
- Generate clear summaries of what changed in the cart.

### Juan
- Implement or refine `backend/product_semantic_index.py` for broader recall.
- Rank alternatives based on category fit, package fit, and stock availability.
- Expand the eval suite with recipe and substitution scenarios.

### Mariia
- Design UI states for substitutions and missing products.
- Make the cart and chat thread readable after larger multi-item updates.
- Add visual affordances that explain why the assistant changed something.

### Nacho
- Persist preferences and order history cleanly in SQLite.
- Ensure chat context assembly in the backend is stable and easy to debug during the hackathon.
- Cover the highest-risk recipe and substitution behaviors with a small targeted test set.

### Unite gate
- ✅ Queries like `quiero hacer una torta` and `armame desayunos para la semana` add several relevant items.
- ✅ Out-of-stock products never silently disappear: they are substituted or explicitly called out.
- ✅ The reply explains the result in a way the user can audit.
- ✅ Automated eval scenarios exist for recipes, multi-item goals, and alternatives.

## VERSION3 🔜 Active
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
- ⬜ Ambiguous requests such as `gaseosa cola 1.5L` open a clarification popup instead of silently guessing.
- ⬜ User selection resumes the exact pending task and updates the cart correctly.
- ⬜ Offer-aware choices are visible and explainable in the UI.
- ⬜ The clarification and offers scenarios pass in the eval suite.

## VERSION4 ⬜
## Next phase

**Full automation with a monthly buys tab**

**Expected completion time:** 7 to 10 hours

**Goal:** turn the assistant into a recurring shopping system

### Concrete deliverable
A user can configure a recurring monthly shopping profile and generate a monthly cart automatically from saved preferences, order history, must-have items, budget constraints, and current offers. The main outcome is not background scheduling; the main outcome is one-click monthly cart generation with stored configuration.

### Architecture shift: deterministic tool layer

The agent currently makes every product decision (is this ambiguous? which to pick? how many? substitute or report?) through prompt instructions the LLM must follow. V4 moves those decisions into the tools so the LLM only orchestrates.

```
Current:  user → LLM (judges + decides) → tools (execute)
V4:       user → LLM (decomposes intent) → tools (decide + execute) → LLM (routes on verdict)
```

### Technical scope
- Add a new monthly-buys tab with persistent configuration.
- Store recurring shopping inputs in SQLite: household size, monthly budget, priority items, preferred brands, strict-brand flag, excluded categories, and free-text notes.
- Build a monthly cart generation flow that merges the recurring config with prior orders and current catalog availability using rule-based logic — not LLM reasoning.
- Server-side cart accumulation: the agent calls `add_to_cart(product_id, quantity)` per item instead of maintaining the full cart list in its context window.
- Preview the generated basket before save, including what was repeated, swapped, skipped, or newly suggested.

### Jeremias
- Swap `search_products` tool for `resolve_product` + `add_to_cart` in the agent graph once Juan and Nacho deliver those.
- System prompt simplifies to ~4 routing rules: if `resolved` → add_to_cart; if `needs_clarification` → request_clarification; if `not_found` → report_missing.
- Add monthly basket graph node: calls `POST /api/v1/recurring-plan/generate`, presents result, handles user overrides.
- Make recurring generation reproducible enough that the team can review and trust the output live.

### Juan
- `resolve_product(query, quantity, catalog)` in `product_semantic_index.py`: wraps `search()` → `build_clarification_candidates()` → `find_alternatives()` into a single verdict (`resolved` / `needs_clarification` / `not_found`). Eliminates ambiguity judgment, product selection, and substitute-vs-report decisions from the LLM.
- `parse_quantity(message)` utility: regex + Spanish word-number map ("dos" → 2, "3 botellas" → 3, never confuses "1L" size with quantity).
- `generate_monthly_basket_candidates(prefs, order_history, catalog, budget)`: rule-based algorithm. Pulls must-haves from preferences, counts frequency across order history, resolves each via `resolve_product()`, fills remaining budget with high-discount items. Returns candidates tagged `must_have` / `recurring` / `offer` / `suggested`. LLM presents the result — it does not compute it.
- Eval cases for monthly restock, budget pressure, and missing essentials.

### Mariia
- Design the monthly config tab and make it feel like a product, not an admin form.
- Show a before/after preview comparing the proposed monthly cart with the last saved plan or last order.
- Make approval, override, and re-run flows extremely clear.

### Nacho
- `session_carts` SQLite table + `add_to_cart` / `remove_from_cart` / `get_cart` endpoints: server-side cart state so the agent never needs to track or merge the cart list across turns.
- `recurring_plans` and `recurring_plan_items` SQLite tables with full CRUD endpoints.
- `POST /api/v1/recurring-plan/generate` → calls Juan's `generate_monthly_basket_candidates()`, returns proposed cart.
- `POST /api/v1/recurring-plan/accept` → saves proposed basket as a new order.
- Own the final integration path from recurring config to generated cart to saved order.

### Unite gate
- ⬜ User can save a recurring monthly profile and load it later.
- ⬜ Monthly generation builds a full proposed cart using config plus history plus current stock/offers.
- ⬜ The generated cart is reviewable and saveable as a normal order.
- ⬜ Automation scenarios are demoable without hidden manual setup.

## Cross-version unite checklist
- ✅ At the end of every version, merge to one demo branch and run the smallest useful verification set.
- ✅ From `version2` onward, run the LLM judge suite and track only the scenarios that protect the live demo.
- Do not start the next version until the current version has one reproducible happy-path demo.
- Keep the external stack stable: vanilla frontend, Python stdlib backend, SQLite, JSON snapshot/index, OpenAI-based agent layer.
- Prefer additive changes over rewrites; the fastest path is to evolve the current code shape, not replace it.

Built around the current proxy-store structure and intentionally optimized for hackathon demo quality.
