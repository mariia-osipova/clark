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
    Generates one embedding per product using a local sentence-transformers model
    (no API key required) and writes data/product_semantic_index.json.

    Model: paraphrase-multilingual-MiniLM-L12-v2
      - Multilingual — works well with Spanish product names
      - 384-dimensional embeddings
      - Downloaded automatically from HuggingFace on first run (~450 MB)

    V2: swap search() to load this index and rank by cosine similarity.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError(
            "sentence-transformers not installed. Run: pip install sentence-transformers"
        )

    model_name = "paraphrase-multilingual-MiniLM-L12-v2"
    print(f"  Loading model {model_name} (downloads on first run)…")
    model = SentenceTransformer(model_name)

    texts = [_product_text(p) for p in catalog]
    ids = [p["id"] for p in catalog]

    print(f"  Embedding {len(texts)} products…")
    # encode() returns a numpy array; convert to plain Python lists for JSON
    vectors = model.encode(texts, batch_size=64, show_progress_bar=True, convert_to_numpy=True)

    entries = [
        {"id": pid, "embedding": vec.tolist()}
        for pid, vec in zip(ids, vectors)
    ]

    index = {
        "version": "v0",
        "model": model_name,
        "entries": entries,
    }
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)
    print(f"  Index written: {len(entries)} embeddings → {INDEX_PATH}")


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


def _product_text(product: dict) -> str:
    """Compact text representation of a product used for embedding."""
    parts = [
        product.get("name", ""),
        product.get("brand", ""),
        product.get("category", ""),
        product.get("package_size", ""),
    ]
    return " ".join(p for p in parts if p)


