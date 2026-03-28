# Juan — Catalog, Ranking & Evaluation

You are assisting Juan. His focus is catalog scraping, product normalization, semantic ranking, and the evaluation harness.

## Files he owns
- `backend/product_semantic_index.py` — semantic retrieval and ranking
- `backend/llm_eval_harness.py` — LLM judge eval suite
- `scripts/run_llm_judge_eval.py` — eval runner
- `data/catalog_snapshot.json` — normalized catalog source
- `data/product_semantic_index.json` — semantic index

## Current version: VERSION3 active

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

### V3 🔜
- [ ] Extend `rank_candidates()` discount weight (current `* 0.01` is too small for offers-aware ranking)
- [ ] Define "materially different" candidate sets for clarification (brand mismatch, size delta >20%, price delta >15%)
- [ ] Expand evals for ambiguous cola, size conflicts, close-brand choices

### V4 ⬜
- [ ] Use order history + offers data to rank monthly basket candidates
- [ ] Bundle-level reasoning so monthly cart is coherent, not item-by-item greedy
- [ ] Eval cases for monthly restock, budget pressure, and missing essentials

## Version roadmap (Juan)
| Version | Focus | Status |
|---|---|---|
| V0 | Scraper, normalization, first-pass query filter | ✅ Done |
| V1 | Product ranking for exact and near-exact matches, filter unavailable items, initial tests | ✅ Done |
| V2 | Semantic retrieval in `product_semantic_index.py`, rank alternatives, expand eval suite | ✅ Done |
| V3 | Extend ranking with discount/offer awareness, define clarification candidate sets | 🔜 Active |
| V4 | Use order history + offers for monthly basket ranking, bundle-level reasoning | ⬜ Upcoming |

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
