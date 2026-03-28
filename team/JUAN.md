# Juan — Catalog, Ranking & Evaluation

You are assisting Juan. His focus is catalog scraping, product normalization, semantic ranking, and the evaluation harness.

## Files he owns
- `backend/product_semantic_index.py` — semantic retrieval and ranking
- `backend/llm_eval_harness.py` — LLM judge eval suite
- `scripts/run_llm_judge_eval.py` — eval runner
- `data/catalog_snapshot.json` — normalized catalog source
- `data/product_semantic_index.json` — semantic index

## Current version: VERSION0 tasks
- [ ] Implement the scraper/normalizer for catalog items (Carrefour public catalog)
- [ ] Standardize product fields: ID, name, brand, package size, price, image URL, available quantity
- [ ] Add a first-pass query filter by product name/brand
- [ ] Write output to `data/catalog_snapshot.json`

## Version roadmap (Juan)
| Version | Focus |
|---|---|
| V0 | Scraper, normalization, first-pass query filter |
| V1 | Product ranking for exact and near-exact matches, filter unavailable items, initial tests |
| V2 | Semantic retrieval in `product_semantic_index.py`, rank alternatives, expand eval suite |
| V3 | Extend ranking with discount/offer awareness, define clarification candidate sets |
| V4 | Use order history + offers for monthly basket ranking, bundle-level reasoning |

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
