# Juan — Catalog, Ranking & Evaluation

You are assisting Juan. His focus is catalog scraping, product normalization, semantic ranking, and the evaluation harness.

## Files he owns
- `backend/product_semantic_index.py` — semantic retrieval and ranking
- `backend/llm_eval_harness.py` — LLM judge eval suite
- `scripts/run_llm_judge_eval.py` — eval runner
- `data/catalog_snapshot.json` — normalized catalog source
- `data/product_semantic_index.json` — semantic index

## Current version: VERSION4 active

### V0 ✅
- [x] Implement the scraper/normalizer for catalog items (Carrefour public catalog)
- [x] Standardize product fields: ID, name, brand, package size, price, image URL, available quantity
- [x] Add a first-pass query filter by product name/brand
- [x] Write output to `data/catalog_snapshot.json`

### V1 ✅
- [x] Product ranking for exact and near-exact matches (`rank_candidates()` with brand +5, size +5, discount boost)
- [x] Unit normalisation (1L == 1000ml == 1 litro) via `_normalize_size()`
- [x] Filter unavailable items before ranking output
- [x] 22 unit tests in `tests/test_product_ranking.py`

### V2 ✅
- [x] Hybrid semantic search in `search()` using sentence-transformers; keyword fallback when index absent
- [x] `find_alternatives(query, catalog, category, top_k)` for stock-aware substitution
- [x] Module-level model and index caching (`_MODEL_CACHE`, `_INDEX_CACHE`)
- [x] V2 eval scenarios: `v2_recipe_torta`, `v2_out_of_stock`, `v2_broad_query`, `v2_out_of_stock_substitution`
- [x] LLM judge (gpt-4o-mini) for v2-tagged scenarios; `--no-llm-judge` flag for CI
- [x] `min_cart_size` + `expected_min_quantity` fields on `Scenario` — v1 scenarios now validate cart
- [x] `_cosine()` fixed to true cosine similarity (was returning raw dot product)
- [x] Strict verdict parsing in `_llm_judge()`; API errors return FAIL not PASS
- [x] 27 unit tests passing, 1 skipped (semantic broad query — requires sentence-transformers install)

### V3 ✅
- [x] Extend `rank_candidates()` discount weight (current `* 0.01` is too small for offers-aware ranking)
- [x] Define "materially different" candidate sets for clarification (brand mismatch, size delta >20%, price delta >15%)
- [x] Expand evals for ambiguous cola, size conflicts, close-brand choices

### V4 🔜 (Robustness Rebuild — Active)

#### Immediate bug fixes (do these first)
- [ ] **Bug #1 CRITICAL** — `chat_agent_agentic.py:743-744`: validate `chosen_option_id` against the stored clarification options for the matching `pending_request_id`. Any product_id is currently accepted, allowing arbitrary products to be added.
- [ ] **Bug #2 CRITICAL** — `chat_agent_agentic.py:483-496`: replace substring match in `resolved_queries` lookup with exact key match on `original_query`. Substring matching can miss or collide, causing the clarification modal to re-trigger infinitely.
- [ ] **Bug #7 MEDIUM** — `product_semantic_index.py:264-266`: `must_haves` bypass the budget gate. Track `budget_used` for all items including must-haves; add `budget_overflow: bool` to the return dict so the caller can warn the user.

#### Phase 1 — Clarification state machine hardening
- [ ] After clarification reply validation, store resolved product using exact `original_query` string as key (not substring)
- [ ] Add guard in `tools_node`: if `resolve_product()` is called for a query already in `resolved_queries`, return cached result immediately (no re-resolution)
- [ ] Add `max_clarification_turns=1` guard: if clarification is already pending in state, `tools_node` must not generate a second one — call `report_missing` instead
- [ ] Add tests in `tests/test_chat_agent.py` for: (a) valid chosen_option_id, (b) invalid chosen_option_id rejected, (c) no infinite loop on re-invoke

#### Phase 2 — Replace ReAct loop with phase-based graph
Replace the generic `agent → tools → loop` with explicit deterministic nodes. LLM only participates in `classify_turn` and `summarize`.

Graph shape:
```
classify_turn → plan_items → resolve_items → apply_cart → summarize
                                  ↓ (needs_clarification)
                            emit_clarification → END
```

New typed state to add to `chat_agent_agentic.py`:
```python
class TurnKind(str, Enum):
    shopping = "shopping"
    clarification_reply = "clarification_reply"
    monthly_basket = "monthly_basket"
    smalltalk = "smalltalk"

@dataclass
class PlannedItem:
    query: str
    quantity: int
    constraints: dict  # brand, size, exclusions from user phrasing

@dataclass
class ResolutionResult:
    planned_item: PlannedItem
    verdict: str  # "resolved" | "needs_clarification" | "not_found"
    product: dict | None
    options: list[dict] | None

class ShopState(TypedDict):
    turn_kind: TurnKind
    planned_items: list  # list[PlannedItem]
    resolutions: list    # list[ResolutionResult]
    pending_clarification: dict | None
    resolved_cart: list[dict]
    missing_items: list[str]
    reply: str
```

Node responsibilities:
- `classify_turn`: single LLM call → `TurnKind` + `PlannedItem[]` extracted in one shot
- `resolve_items`: calls `resolve_product()` for each item; stops at first `needs_clarification`; already-resolved items stay in `resolved_cart`
- `apply_cart`: upserts resolved items to `session_carts` — pure DB writes, no LLM
- `emit_clarification`: formats `PendingClarification` for first unresolved item, returns END
- `summarize`: LLM call receives structured `TurnResultFacts` (not raw tool history); falls back to deterministic template if LLM unavailable

**Key invariant:** `handle_chat()` return shape stays identical throughout the refactor.

#### Phase 3 — `resolve_product()` as single source of truth
- [ ] Wire `resolve_items` node to call `resolve_product()` for every `PlannedItem` — no LLM judgment on ambiguity
- [ ] Remove prompt-based "decide if this is ambiguous" instructions from system prompt
- [ ] Ensure `resolve_product()` handles all four cases: exact match, substitute-eligible, needs_clarification, not_found

#### Phase 4 — `generate_monthly_basket_candidates()` cleanup
- [ ] Add `budget_overflow: bool` to return dict (fix for bug #7)
- [ ] Validate all three tags (`must_have`, `recurring`, `offer`) are used consistently
- [ ] Signature must be: `generate_monthly_basket_candidates(prefs: dict, order_history: list, catalog: list, budget: float) -> list[dict]`
- [ ] This is the single function both the chat action and REST endpoint call — no alternative code path

#### Phase 5 — Clarification resume for multi-item requests
- [ ] Persist `resolved_so_far: list[ResolutionResult]` inside `PendingClarification` dict
- [ ] On clarification reply, inject resolved items directly into `ShopState.resolutions` before graph runs
- [ ] `resolve_items` node skips items already in `resolutions`, only processes remaining `PlannedItem[]`

#### Phase 6 — Eval suite expansion
Prerequisite: add multi-turn scenario support to `run_llm_judge_eval.py` (shared `session_id` across turns within a scenario).

Add `expected_product_ids` to ALL existing 13 scenarios (currently empty everywhere).

New scenario groups to add to `llm_eval_harness.py`:

**Group A — Clarification resume (zero coverage today)**
- `clarification_resume_single`: ambiguous yogur → user picks → agent adds
- `clarification_resume_recipe`: tiramisú → mascarpone ambiguity → user picks → 5+ remaining items added

**Group B — Multi-item partial resolution**
- `partial_recipe_oos`: 5-item breakfast, 2 OOS → cart has 3, `missing_items` has 2
- `multi_item_mixed_certainty`: "leche, yogur, crema" → exact / substitute / not_found, one of each

**Group C — Quantity parsing**
- `quantity_spanish_words`: "dos yogures" → qty=2
- `quantity_no_size_confusion`: "3 botellas de leche 1L" → qty=3, not confused with size
- `quantity_duplicate_request`: same item twice → qty=2 upsert, not two rows

**Group D — Cart idempotency** (requires multi-turn harness)
- `session_cart_multi_turn`: turn1 add A, turn2 add B → cart=[A,B]
- `session_cart_upsert`: add 1 leche twice → qty=2

**Group E — Monthly basket**
- `monthly_basket_must_haves`: must_haves always in proposed_cart
- `monthly_basket_budget_cap`: must_haves + recurring stays ≤ budget; `budget_overflow: true` if exceeded
- `monthly_basket_recurring_detection`: items in 2+ orders get `recurring` tag

Judge improvements:
- Replace vague PASS/FAIL reason string with structured checks: `cart_size_ok`, `product_ids_match`, `missing_items_populated`, `no_clarification_leak`
- Pass catalog subset (names + IDs of cart items) to judge for product grounding

## Version roadmap (Juan)
| Version | Focus | Status |
|---|---|---|
| V0 | Scraper, normalization, first-pass query filter | ✅ Done |
| V1 | Product ranking for exact and near-exact matches, filter unavailable items, initial tests | ✅ Done |
| V2 | Semantic retrieval in `product_semantic_index.py`, rank alternatives, expand eval suite | ✅ Done |
| V3 | Extend ranking with discount/offer awareness, define clarification candidate sets | ✅ Done |
| V4 | `resolve_product`, `parse_quantity`, `generate_monthly_basket_candidates` — deterministic tool layer | 🔜 Active |

## How to help Juan
- When he asks to scrape or update the catalog, write output to `data/catalog_snapshot.json` with normalized fields.
- When adding ranking logic, keep it in `product_semantic_index.py` as pure functions where possible.
- When adding eval cases, append to the harness in `llm_eval_harness.py` and document the scenario.
- Log scraper runs and catalog updates in [docs/LOG.md](../docs/LOG.md).

## Normalized product fields (standard)
```json
{
  "id": "string",
  "name": "string",
  "brand": "string",
  "package_size": "string",
  "price": 0.00,
  "image_url": "string",
  "available_quantity": 0,
  "category": "string",
  "discount_pct": 0.0
}
```

## Key conventions
- Never hardcode prices or quantities — always pull from scrape.
- Unavailable items (quantity 0) must be filtered before ranking output.
- Eval scenarios must include at least: exact match, near-exact match, and unavailable item.
- `find_alternatives()` is the substitution API — Jeremias wires it into the agent tool for V3.
- Use `--no-llm-judge` when running evals in CI to avoid API dependency.
