# Juan — Catalog, Ranking & Evaluation

You are assisting Juan. His focus is catalog scraping, product normalization, semantic ranking, and the evaluation harness.

## Files he owns
- `backend/product_semantic_index.py` — semantic retrieval and ranking
- `backend/llm_eval_harness.py` — LLM judge eval suite
- `scripts/run_llm_judge_eval.py` — eval runner
- `data/catalog_snapshot.json` — normalized catalog source
- `data/product_semantic_index.json` — semantic index

## Current version: VERSION4 active

### V3 ✅
- [x] Extend `rank_candidates()` discount weight and tiered scoring (40%→+4, 20%→+2, 10%→+1)
- [x] `build_clarification_candidates()`: brand mismatch, size delta >20%, price delta >15% triggers popup
- [x] `_candidates_are_ambiguous()` deterministic ambiguity gate
- [x] Clean up modal label: removed duplicate brand prefix
- [x] V3 eval scenarios: `ambiguous_cola`, `ambiguous_brand`, `ambiguous_size`, `offers_ranking`

### Current focus (V4)
- [ ] `resolve_product(query, quantity, catalog)` in `product_semantic_index.py`: wraps `search()` → `build_clarification_candidates()` → `find_alternatives()` into a single verdict (`resolved` / `needs_clarification` / `not_found`)
- [ ] `parse_quantity(message)`: regex + Spanish word-number map ("dos" → 2, "3 botellas" → 3, never confuses "1L" size with quantity)
- [ ] `generate_monthly_basket_candidates(prefs, order_history, catalog, budget)`: rule-based, returns candidates tagged `must_have` / `recurring` / `offer` / `suggested`
- [ ] Eval cases for monthly restock, budget pressure, and missing essentials

## Version roadmap (Juan)
| Version | Focus | Status |
|---|---|---|
| V0 | Scraper, normalization, first-pass query filter | ✅ Done |
| V1 | Product ranking for exact and near-exact matches, filter unavailable items, initial tests | ✅ Done |
| V2 | Semantic retrieval in `product_semantic_index.py`, rank alternatives, expand eval suite | ✅ Done |
| V3 | Discount-aware ranking, clarification candidate sets, eval scenarios | ✅ Done |
| V4 | `resolve_product`, `parse_quantity`, `generate_monthly_basket_candidates` | 🔜 Active |

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
- `find_alternatives()` is used internally by `resolve_product()` — Jeremias wires `resolve_product` into the agent for V4, not `find_alternatives` directly.
- Use `--no-llm-judge` when running evals in CI to avoid API dependency.
