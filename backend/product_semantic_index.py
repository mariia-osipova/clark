"""
Semantic retrieval and product ranking.
Owner: Juan

Provides:
    search(query, catalog, top_k) -> list[dict]
    rank_candidates(candidates, query) -> list[dict]
    build_index(catalog) -> None   (writes data/product_semantic_index.json)
"""

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "data" / "product_semantic_index.json"
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


# ─── Public API ──────────────────────────────────────────────────────────────

def search(query: str, catalog: list[dict], top_k: int = 10) -> list[dict]:
    """
    Return up to top_k products matching the query.
    V0: simple keyword filter on name + brand.
    V2: upgrade to semantic retrieval using the index.
    """
    tokens = _tokenize(query)
    scored = []
    for product in catalog:
        if product.get("available_quantity", 1) == 0:
            continue
        score = _keyword_score(tokens, product)
        if score > 0:
            scored.append((score, product))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_k]]


def rank_candidates(candidates: list[dict], query: str) -> list[dict]:
    """
    Re-rank a pre-filtered candidate list by relevance to query.
    Considers: keyword match, discount, availability.
    """
    tokens = _tokenize(query)
    scored = []
    for p in candidates:
        score = _keyword_score(tokens, p)
        score += p.get("discount_pct", 0) * 0.01  # small boost for discounts
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored]


def build_index(catalog: list[dict]) -> None:
    """
    Build and persist a semantic index from the catalog.
    V0: no-op (keyword search is sufficient).
    V2: implement embedding-based index here.
    """
    index = {"version": "v0", "product_ids": [p["id"] for p in catalog]}
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Index built: {len(catalog)} products → {INDEX_PATH}")


# ─── Internal ────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _keyword_score(tokens: list[str], product: dict) -> float:
    searchable = " ".join([
        product.get("name", ""),
        product.get("brand", ""),
        product.get("category", ""),
        product.get("package_size", ""),
    ]).lower()
    return sum(1 for t in tokens if t in searchable)
