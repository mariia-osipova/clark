# Juan — Agent Graph, Frontend & Evaluation

You are assisting Juan. His focus is the agent graph logic, response contract, frontend integration, and the evaluation harness.

## Files he owns
- `backend/chat_agent_agentic.py` — main agentic shopping flow (graph logic; classifier prompt is shared with Jeremias)
- `backend/chat_agent.py` — thin compatibility layer
- `frontend/app.js` — frontend chat and cart UI
- `backend/llm_eval_harness.py` — LLM judge eval suite
- `scripts/run_llm_judge_eval.py` — eval runner
- `data/catalog_snapshot.json` — normalized catalog source
- `data/product_semantic_index.json` — semantic index

## Current version: VERSION4 active — Resolve-First rebuild

### Completed work (V0–V3)
- [x] Scraper/normalizer, catalog snapshot, product ranking, semantic search, eval harness (V0–V3 shipped)
- [x] Phase-based graph replacing ReAct loop (V4 shipped)
- [x] `resolve_product` verdict `needs_suggestion` (V4 shipped)

### V4 — Resolve-First Agent Graph (active)

#### A — Bug fixes (do these first)
- [ ] **Bug #1 CRITICAL** — `chat_agent_agentic.py`: validate `chosen_option_id` against stored clarification options for the matching `pending_request_id`. Any product_id is currently accepted.
- [ ] **Bug #2 CRITICAL** — `chat_agent_agentic.py`: replace substring match in `resolved_queries` lookup with exact key match on `original_query`. Substring matching causes infinite clarification re-trigger.

#### B — Resolve-first graph changes
- [ ] Change `resolve_items` so it processes the **full basket** — never stop before all clear items are handled
- [ ] Make `apply_cart` persist resolved items even when a follow-up is still needed
- [ ] Keep at most one blocking clarification per turn; if multiple items are low-confidence, store only the first and defer the rest to later turns
- [ ] Add a separate non-blocking `suggestions` payload for unavailable items — suggestions must never masquerade as clarification

#### C — Response contract changes
- [ ] Change the chat response so `cart` may be present even when `clarification` is present — cart updates first, clarification becomes a rare follow-up, not a gate
- [ ] Add `suggestions: [{ query, reason: "out_of_stock" | "not_found", options: [...] }]` to the chat response
- [ ] Keep `clarification` only for genuine ambiguity between plausible in-stock matches
- [ ] Update `summarize` to confirm what was added, mention missing/suggested items, end with `¿Está bien así?`

#### D — Frontend changes
- [ ] In `frontend/app.js`, always apply the returned authoritative cart when `cart` is present
- [ ] Keep the existing modal only for `clarification`
- [ ] Render `suggestions` inline in the chat thread as optional alternative cards with direct `Agregar` actions using the existing cart API — suggestions must not block the conversation

#### E — Clarification state machine hardening
- [ ] After clarification reply, store resolved product using exact `original_query` key (not substring)
- [ ] Add guard: if `resolve_product()` is called for a query already in `resolved_queries`, return cached result immediately
- [ ] Add `max_clarification_turns=1` guard: if clarification is already pending, call `report_missing` instead
- [ ] Add tests in `tests/test_chat_agent.py` for: valid/invalid `chosen_option_id`, no infinite loop on re-invoke

#### F — Clarification resume for multi-item requests
- [ ] Persist `resolved_so_far` inside `PendingClarification` dict
- [ ] On clarification reply, inject resolved items into `ShopState.resolutions` before graph runs
- [ ] `resolve_items` skips already-resolved items, only processes remaining `PlannedItem[]`

#### G — Eval suite expansion
Prerequisite: multi-turn scenario support in `run_llm_judge_eval.py`.

New scenario groups for `llm_eval_harness.py`:

- **Group A — Clarification resume**: `clarification_resume_single`, `clarification_resume_recipe`
- **Group B — Multi-item partial resolution**: `partial_recipe_oos`, `multi_item_mixed_certainty`
- **Group C — Quantity parsing**: `quantity_spanish_words`, `quantity_no_size_confusion`, `quantity_duplicate_request`
- **Group D — Cart idempotency** (multi-turn): `session_cart_multi_turn`, `session_cart_upsert`
- **Group E — Monthly basket**: `monthly_basket_must_haves`, `monthly_basket_budget_cap`, `monthly_basket_recurring_detection`
- Add `expected_product_ids` to all existing 13 scenarios
- Replace vague PASS/FAIL with structured checks: `cart_size_ok`, `product_ids_match`, `missing_items_populated`, `no_clarification_leak`

#### H — API-level tests
- [ ] Add API tests for: `cart` + `clarification` coexistence, `suggestions` without clarification, "no automatic substitute" for unavailable items
- [ ] Replace "stop at first clarification" graph test with resolve-first behavior: clear items added immediately, cart returned, only unresolved item opens clarification

## How to help Juan
- When he describes a graph or flow change, implement it in `chat_agent_agentic.py`.
- When he describes frontend changes, implement them in `frontend/app.js`.
- When adding eval cases, append to the harness in `llm_eval_harness.py`.
- Log changes in [docs/LOG.md](../docs/LOG.md).
- Cross-reference [docs/api.md](../docs/api.md) for the chat endpoint contract before changing payloads.

## Key conventions
- The agentic flow lives in `chat_agent_agentic.py`. `chat_agent.py` is only a compatibility shim.
- Do not patch agent behavior with ad hoc phrase rules. Enforce behavior through LangGraph nodes, edges, state transitions, or tool-grounded runtime invariants.
- Cart mutations must be validated server-side before returning to the UI.
- `handle_chat()` return shape must remain stable throughout the refactor.
- Reply tone: helpful, concise, Spanish-aware (users may write in Spanish).
