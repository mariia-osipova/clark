"""
Semantic retrieval and product ranking.
Owner: Juan

Provides:
    search(query, catalog, top_k) -> list[dict]
    rank_candidates(candidates, query) -> list[dict]
    find_alternatives(query, catalog, category, top_k) -> list[dict]
    build_index(catalog) -> None   (writes data/product_semantic_index.json)
"""

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "data" / "product_semantic_index.json"
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"

# Module-level cache: avoids reloading model and index on every call
_MODEL_CACHE: dict = {}   # key "model" → SentenceTransformer instance
_INDEX_CACHE: dict = {}   # key "entries" → list[{id, embedding}], "path_mtime" → float


# ─── Public API ──────────────────────────────────────────────────────────────

def search(query: str, catalog: list[dict], top_k: int = 10) -> list[dict]:
    """
    Return up to top_k products matching the query, re-ranked by brand/size signals.
    V2: semantic retrieval → rank_candidates (brand +5, size +5, discount boost).
    Falls back to keyword filter if the semantic index is not available.
    """
    candidates = _semantic_candidates(query, catalog, top_k * 3)
    if not candidates:
        # Fallback: keyword filter (V0 behaviour, works without an index)
        tokens = _tokenize(query)
        candidates = [
            p for p in catalog
            if p.get("available_quantity", 1) != 0 and _keyword_score(tokens, p) > 0
        ]
        return rank_candidates(candidates, query)[:top_k]

    # Semantic candidates: re-rank by keyword/brand/size signals.
    # If none score above zero (broad query like "para el desayuno"), preserve semantic order.
    reranked = rank_candidates(candidates, query)
    return (reranked if reranked else candidates)[:top_k]


def rank_candidates(candidates: list[dict], query: str) -> list[dict]:
    """
    Re-rank a pre-filtered candidate list by relevance to query.
    Considers: keyword match, exact brand match, exact package-size match, discount, availability.
    Out-of-stock items are excluded.
    """
    tokens = _tokenize(query)
    scored = []
    for p in candidates:
        if p.get("available_quantity", 1) == 0:
            continue
        score = _keyword_score(tokens, p)
        score += _brand_score(tokens, p)          # +5 for exact brand match
        score += _size_score(query, p)            # +5 for exact package-size match
        score += p.get("discount_pct", 0) * 0.01  # small boost for discounts
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored]


def find_alternatives(
    query: str,
    catalog: list[dict],
    category: str | None = None,
    top_k: int = 3,
) -> list[dict]:
    """
    Return up to top_k in-stock alternatives when the exact match is unavailable.
    Filters to the given category first; if that yields nothing, searches the full catalog.
    Out-of-stock items are always excluded.
    """
    in_stock = [p for p in catalog if p.get("available_quantity", 1) != 0]

    pool = in_stock
    if category:
        category_pool = [p for p in in_stock if p.get("category", "") == category]
        if category_pool:
            pool = category_pool

    candidates = _semantic_candidates(query, pool, top_k * 3)
    if not candidates:
        tokens = _tokenize(query)
        candidates = [p for p in pool if _keyword_score(tokens, p) > 0]
        if not candidates:
            # Last resort: return anything in-stock from pool, skip re-ranking
            return pool[:top_k]

    return rank_candidates(candidates, query)[:top_k]


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
    searchable = _normalize_text(" ".join([
        product.get("name", ""),
        product.get("brand", ""),
        product.get("category", ""),
        product.get("package_size", ""),
    ]))
    return sum(1 for t in tokens if _normalize_text(t) in searchable)


def _product_text(product: dict) -> str:
    """Compact text representation of a product used for embedding."""
    parts = [
        product.get("name", ""),
        product.get("brand", ""),
        product.get("category", ""),
        product.get("package_size", ""),
    ]
    return " ".join(p for p in parts if p)


def _normalize_text(s: str) -> str:
    """Lowercase and strip accents for accent-insensitive comparisons."""
    nfkd = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Multipliers to convert every liquid/solid unit to a canonical base (ml or g)
_LIQUID_UNITS = {
    "ml": 1, "cc": 1,
    "l": 1000, "lt": 1000, "lts": 1000, "litro": 1000, "litros": 1000,
}
_SOLID_UNITS = {
    "g": 1, "gr": 1, "gramo": 1, "gramos": 1,
    "kg": 1000, "kilo": 1000, "kilos": 1000,
}
# Longer alternatives must come first so the regex engine doesn't stop early
_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(ml|cc|litros|litro|lts|lt|l|kg|kilos|kilo|gramos|gramo|gr|g)\b",
    re.IGNORECASE,
)


def _normalize_size(text: str) -> tuple[float, str] | None:
    """
    Parse the first size expression in *text* and return (canonical_value, unit_class).
    unit_class is 'liquid' (base: ml) or 'solid' (base: g).
    Returns None if no recognisable size is found.

    Examples:
        "1L"       → (1000.0, 'liquid')
        "1000ml"   → (1000.0, 'liquid')
        "500 ml"   → (500.0,  'liquid')
        "1 litro"  → (1000.0, 'liquid')
        "200g"     → (200.0,  'solid')
        "1kg"      → (1000.0, 'solid')
    """
    m = _UNIT_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit in _LIQUID_UNITS:
        return (value * _LIQUID_UNITS[unit], "liquid")
    if unit in _SOLID_UNITS:
        return (value * _SOLID_UNITS[unit], "solid")
    return None


def _load_model():
    """Lazy-load the sentence-transformer model, cached at module level."""
    if "model" not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer
        _MODEL_CACHE["model"] = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _MODEL_CACHE["model"]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (assumed unit-normalised by sentence-transformers)."""
    dot = sum(x * y for x, y in zip(a, b))
    return dot


def _load_index() -> list[dict]:
    """Load and cache the semantic index from disk. Returns [] if index missing."""
    try:
        mtime = INDEX_PATH.stat().st_mtime
    except FileNotFoundError:
        return []

    if _INDEX_CACHE.get("path_mtime") != mtime:
        with open(INDEX_PATH) as f:
            data = json.load(f)
        _INDEX_CACHE["entries"] = data.get("entries", [])
        _INDEX_CACHE["path_mtime"] = mtime

    return _INDEX_CACHE["entries"]


def _semantic_candidates(query: str, catalog: list[dict], top_k: int) -> list[dict]:
    """
    Encode query, compute cosine similarity against the persisted index,
    and return the top_k in-stock products ordered by similarity.
    Returns [] if sentence-transformers is not installed or index is missing.
    """
    try:
        model = _load_model()
    except (ImportError, Exception):
        return []

    entries = _load_index()
    if not entries:
        return []

    # Build a fast id → product lookup (only in-stock items)
    id_to_product = {
        p["id"]: p for p in catalog if p.get("available_quantity", 1) != 0
    }

    query_vec = model.encode([query], convert_to_numpy=True)[0].tolist()

    scored = []
    for entry in entries:
        pid = entry["id"]
        if pid not in id_to_product:
            continue  # product removed from catalog or OOS
        sim = _cosine(query_vec, entry["embedding"])
        scored.append((sim, id_to_product[pid]))

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_k]]


def _brand_score(query_tokens: list[str], product: dict) -> float:
    """
    Return 5.0 if the product brand appears (accent-insensitive) among the
    query tokens, 0.0 otherwise.
    Multi-word brands (e.g. 'La Serenísima') also match if every word is present.
    """
    brand = product.get("brand", "")
    if not brand:
        return 0.0
    brand_tokens = _tokenize(_normalize_text(brand))
    norm_query = [_normalize_text(t) for t in query_tokens]
    if all(bt in norm_query for bt in brand_tokens):
        return 5.0
    return 0.0


def _size_score(query: str, product: dict) -> float:
    """
    Return 5.0 if the package size expressed in the query matches the
    product's package_size (after unit normalisation), 0.0 otherwise.
    """
    query_size = _normalize_size(query)
    product_size = _normalize_size(product.get("package_size", ""))
    if query_size and product_size and query_size == product_size:
        return 5.0
    return 0.0


