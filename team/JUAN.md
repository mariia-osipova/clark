# Juan — Catalog, Ranking & Evaluation

You are assisting Juan. His focus is catalog scraping, product normalization, semantic ranking, and the evaluation harness.

## Files he owns
- `backend/product_semantic_index.py` — semantic retrieval and ranking
- `backend/llm_eval_harness.py` — LLM judge eval suite
- `scripts/run_llm_judge_eval.py` — eval runner
- `data/catalog_snapshot.json` — normalized catalog source
- `data/product_semantic_index.json` — semantic index

## Current focus
- [ ] Extend ranking with discount and offer awareness
- [ ] Define the candidate set used for clarification prompts
- [ ] Expand eval coverage for ambiguous cola, size conflicts, and close-brand choices

## Next focus
- [ ] Use order history and offers data for monthly basket ranking once the clarification flow is stable

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
