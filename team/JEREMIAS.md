# Jeremias — Semantic Layer & Classifier

You are assisting Jeremias. His focus is the semantic retrieval and ranking engine, the resolve-first verdict policy, and the classifier/decomposition prompts.

## Files he owns
- `backend/product_semantic_index.py` — semantic retrieval, ranking, and verdict logic
- `tests/test_product_ranking.py` — ranking and resolver unit tests
- `_CLASSIFY_SYSTEM` prompt block in `backend/chat_agent_agentic.py` (classifier only, not graph logic)

## Current version: VERSION4 active — Resolve-First rebuild

## Completed work
- [x] Hybrid search: keyword matches merged into semantic candidate pool
- [x] Discount tiebreaker reduced (0.4/0.2/0.1) — discount can no longer override keyword relevance
- [x] `_SIMILARITY_FLOOR = 0.45` — cross-category semantic noise filtered
- [x] `find_alternatives` last-resort `pool[:top_k]` fallback removed — returns `[]` instead of unrelated products
- [x] `resolve_product` verdict `needs_suggestion` added
- [x] `_CLASSIFY_SYSTEM` tightened with REGLA RECETAS and tiramisu/pizza few-shot decomposition examples

## Resolve-first semantic layer (active)

### A — Candidate evidence model
- [ ] Rework retrieval in `product_semantic_index.py` so each candidate carries structured evidence: semantic similarity, lexical score, explicit brand match, explicit size match, qualifier match (`entera`, `descremada`, `0 lactosa`, `sin TACC`, `light`, `diet`), and stock status
- [ ] Treat explicit user constraints (brand, package size, qualifiers) as **hard requirements**, not soft preferences — candidates that fail a hard constraint must be excluded before scoring

### B — Four-verdict policy
- [ ] Replace the current verdict logic in `resolve_product` with four explicit outcomes: `resolved`, `needs_clarification`, `needs_suggestion`, `not_found`
- [ ] `resolved`: default for generic in-stock requests when top-1 is relevant and clearly ahead of top-2
- [ ] `needs_clarification`: only when top candidates are both relevant, materially different, and close in score
- [ ] `needs_suggestion`: only when the requested item is unavailable/absent but there is a relevant alternative set
- [ ] `not_found`: no relevant match and no relevant alternative cluster
- [ ] Start with a top-score gap default of `0.15` for auto-pick vs. clarification threshold

### C — Fallback removal & noise suppression
- [ ] Remove the fallback where `search()` returns raw semantic candidates when reranking finds no relevance evidence
- [ ] Only surface suggestions when the alternative set is coherent and passes a relevance floor — if the candidate set is noisy or cross-category, return `not_found` instead
- [ ] Ensure `azúcar` and similar queries never surface unrelated vegetables, cleaners, or other semantic noise

### D — Classifier & decomposition prompts
- [ ] Tighten `_CLASSIFY_SYSTEM` so recipe and meal-prep prompts emit catalog-facing ingredient queries instead of vague head nouns
- [ ] Add few-shot JSON examples for recipe decomposition, including tiramisu-like requests
- [ ] Add classifier tests: tiramisu-like inputs must emit `queso mascarpone`, `cafe instantaneo`, `huevos`, `azucar`, etc.

### E — Tests
- [ ] Rewrite ranking tests to protect auto-pick behavior when top-1 is clearly ahead
- [ ] Add regression cases for noisy queries (`azúcar` must never surface unrelated products)
- [ ] Add tests for hard-constraint filtering (brand, size, qualifier tokens)
- [ ] Add tests for each of the four verdicts in isolation

## How to help Jeremias
- When he describes a retrieval or ranking change, implement it in `product_semantic_index.py`.
- When he describes a classifier prompt change, edit `_CLASSIFY_SYSTEM` in `chat_agent_agentic.py`.
- Keep ranking logic as pure functions — avoid side effects.
- When adding tests, put them in `tests/test_product_ranking.py`.
- Log changes in [docs/LOG.md](../docs/LOG.md).

## Key conventions
- Do not patch ranking behavior with ad hoc phrase rules. Behavior must emerge from the scoring model and evidence structure.
- Unavailable items (quantity 0) must be filtered before ranking output.
- `find_alternatives()` must never return cross-category noise — `[]` is better than garbage.
- Explicit user constraints are hard requirements, not preferences.
- Reply tone: helpful, concise, Spanish-aware (users may write in Spanish).
