"""
Semantic retrieval and product ranking.
Owner: Juan

Provides:
    parse_quantity(message) -> int
    extract_constraints(query, catalog) -> dict
    resolve_product(query, quantity, catalog) -> dict
    search(query, catalog, top_k, constraints) -> list[dict]
    rank_candidates(candidates, query) -> list[dict]
    find_alternatives(query, catalog, category, top_k, constraints) -> list[dict]
    build_clarification_candidates(candidates, max_options) -> list[dict]
    generate_monthly_basket_candidates(prefs, order_history, catalog, budget) -> dict
    build_index(catalog) -> None   (writes data/product_semantic_index.json)
"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "data" / "product_semantic_index.json"
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"

# Module-level cache: avoids reloading model and index on every call
_MODEL_CACHE: dict = {}   # key "model" → SentenceTransformer instance
_INDEX_CACHE: dict = {}   # key "entries" → list[{id, embedding}], "path_mtime" → float

# Minimum cosine similarity for a semantic candidate to be considered relevant.
# Filters out cross-category noise (e.g. "azúcar" returning cleaners).
_SIMILARITY_FLOOR = 0.45

# Spanish word-to-number map for parse_quantity
_WORD_TO_NUM: dict[str, int] = {
    "un": 1, "una": 1, "uno": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12,
}


# ─── Public API ──────────────────────────────────────────────────────────────

def parse_quantity(message: str) -> int:
    """
    Extract the intended purchase quantity from a natural-language message.

    Handles:
      - Digit quantities: "3 botellas", "agrega 2 yogures" → 3, 2
      - Spanish word-numbers: "dos paquetes", "tres litros" → 2, 3
      - Default: returns 1 when no quantity is found

    Never confuses size expressions ("1L", "500ml", "200g") with quantity.
    "3 botellas de leche 1L" → 3, not 1.
    """
    # Strip size expressions first so "1L" in "leche 1L" is not treated as qty 1
    stripped = _UNIT_RE.sub("", message)

    # Look for a positive integer in the stripped text
    m = re.search(r"\b([1-9]\d*)\b", stripped)
    if m:
        return int(m.group(1))

    # Fall back to Spanish word-numbers
    for token in _tokenize(stripped):
        if token in _WORD_TO_NUM:
            return _WORD_TO_NUM[token]

    return 1


# Known qualifier tokens that act as hard requirements when present in a query.
_QUALIFIER_TOKENS: set[str] = {
    "entera", "descremada", "0 lactosa", "sin lactosa", "sin tacc",
    "light", "diet", "integral",
}


def extract_constraints(query: str, catalog: list[dict] | None = None) -> dict:
    """
    Parse hard constraints from a natural-language query.

    Returns:
        {
            "brand": str | None,
            "size": (float, str) | None,   # (canonical_value, unit_class)
            "qualifiers": list[str],
        }

    Brand is matched against known brands in the catalog (longest match wins).
    Size reuses _normalize_size.  Qualifiers come from _QUALIFIER_TOKENS.
    """
    norm_query = _normalize_text(query)

    # --- Brand extraction (longest catalog brand that appears in query) ---
    brand: str | None = None
    if catalog:
        best_len = 0
        for p in catalog:
            b = p.get("brand", "")
            if not b:
                continue
            norm_b = _normalize_text(b)
            if norm_b in norm_query and len(norm_b) > best_len:
                brand = b
                best_len = len(norm_b)

    # --- Size extraction ---
    size = _normalize_size(query)

    # --- Qualifier extraction (accent-insensitive) ---
    qualifiers: list[str] = []
    for q in _QUALIFIER_TOKENS:
        if _normalize_text(q) in norm_query:
            qualifiers.append(q)

    return {"brand": brand, "size": size, "qualifiers": qualifiers}


def _apply_hard_constraints(candidates: list[dict], constraints: dict) -> list[dict]:
    """
    Remove candidates that fail any hard constraint.
    If filtering eliminates everything, return the original list (better to show
    something than return not_found when stock exists).
    """
    if not constraints:
        return candidates

    brand = constraints.get("brand")
    size = constraints.get("size")
    qualifiers = constraints.get("qualifiers", [])

    if not brand and not size and not qualifiers:
        return candidates

    filtered = candidates
    if brand:
        norm_brand = _normalize_text(brand)
        filtered = [
            p for p in filtered
            if _normalize_text(p.get("brand", "")) == norm_brand
        ]
    if size:
        target_val, target_class = size
        filtered = [
            p for p in filtered
            if _size_matches(p, target_val, target_class)
        ]
    if qualifiers:
        filtered = [
            p for p in filtered
            if _has_all_qualifiers(p, qualifiers)
        ]

    return filtered if filtered else candidates


def _size_matches(product: dict, target_val: float, target_class: str) -> bool:
    """True if product size is within 5% of the target (same unit class)."""
    ps = _normalize_size(product.get("package_size", ""))
    if not ps:
        return False
    val, cls = ps
    if cls != target_class:
        return False
    return abs(val - target_val) / max(target_val, 1) <= 0.05


def _has_all_qualifiers(product: dict, qualifiers: list[str]) -> bool:
    """True if the product name/category contains all qualifier tokens."""
    searchable = _normalize_text(
        product.get("name", "") + " " + product.get("category", "")
    )
    return all(_normalize_text(q) in searchable for q in qualifiers)


def resolve_product(query: str, quantity: int, catalog: list[dict]) -> dict:
    """
    Single-call product resolution: wraps search → clarification detection → alternatives.

    Returns a verdict dict with one of four statuses:
      resolved            → {status, product: dict, quantity: int, substituted: bool}
      needs_clarification → {status, options: list[dict], quantity: int}
      needs_suggestion    → {status, options: list[dict], quantity: int}
                            (requested item absent/OOS; options are relevant alternatives)
      not_found           → {status, quantity: int}

    Eliminates ambiguity judgment and substitute-vs-report decisions from the LLM.
    The LLM receives this dict and routes accordingly — it does not compute it.
    """
    constraints = extract_constraints(query, catalog)
    results = search(query, catalog, constraints=constraints)

    if not results:
        # Try to find the OOS product's category for better alternative targeting
        tokens = _tokenize(query)
        oos_matches = [
            p for p in catalog
            if p.get("available_quantity", 1) == 0 and _keyword_score(tokens, p) > 0
        ]
        category = oos_matches[0].get("category") if oos_matches else None
        alternatives = find_alternatives(query, catalog, category=category, constraints=constraints)
        if not alternatives:
            return {"status": "not_found", "quantity": quantity}
        return {
            "status": "needs_suggestion",
            "options": alternatives[:3],
            "quantity": quantity,
        }

    # Always auto-pick the top result. The name-anchored pipeline + hard
    # constraints already ensure results are precise. Clarification is reserved
    # exclusively for needs_suggestion (OOS/not-found with alternatives).
    return {
        "status": "resolved",
        "product": results[0],
        "quantity": quantity,
        "substituted": False,
    }


def search(query: str, catalog: list[dict], top_k: int = 4, constraints: dict | None = None) -> list[dict]:
    """
    Return up to top_k products matching the query.

    Pipeline:
      1. Name-field matching on the content query (constraints stripped)
      2. Semantic reranking of name-matched candidates
      3. Semantic fallback with strict similarity floor (0.65)

    *constraints* is an optional dict from extract_constraints() with keys like
    brand, size, qualifiers.  When provided, hard constraint filters are applied.
    """
    # Build content query by stripping constraints
    content_query = _strip_constraints(query, constraints) if constraints else query

    # Stage 1: name-field matching
    candidates = _name_candidates(content_query, catalog)
    if constraints:
        candidates = _apply_hard_constraints(candidates, constraints)

    # Stage 2: semantic rerank
    if candidates:
        candidates = _semantic_rerank(query, candidates)
        return candidates[:top_k]

    # Stage 3: semantic fallback with strict floor
    candidates = _semantic_candidates(query, catalog, top_k * 3, floor=0.65)
    if constraints:
        candidates = _apply_hard_constraints(candidates, constraints)
    # Still apply rank_candidates for keyword/brand/size scoring
    ranked = rank_candidates(candidates, query)
    return (ranked if ranked else candidates)[:top_k]


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
        if score > 0:
            # Tiered discount bonus — tiebreaker for already-relevant products only.
            # Must not fire on products with zero keyword/brand/size match or it
            # promotes irrelevant items above actual matches.
            discount_pct = p.get("discount_pct", 0)
            if discount_pct >= 40:
                score += 0.4    # Strong offer tiebreaker — must not override keyword relevance
            elif discount_pct >= 20:
                score += 0.2    # Meaningful discount tiebreaker
            elif discount_pct >= 10:
                score += 0.1    # Mild discount tiebreaker
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored]


def find_alternatives(
    query: str,
    catalog: list[dict],
    category: str | None = None,
    top_k: int = 3,
    constraints: dict | None = None,
) -> list[dict]:
    """
    Return up to top_k in-stock alternatives when the exact match is unavailable.
    Filters to the given category first; if that yields nothing, searches the full catalog.
    Out-of-stock items are always excluded.

    Note: brand constraint is intentionally NOT applied to alternatives — the user's
    brand is unavailable, so we broaden the search.  Size and qualifiers still apply.
    """
    in_stock = [p for p in catalog if p.get("available_quantity", 1) != 0]

    pool = in_stock
    if category:
        category_pool = [p for p in in_stock if p.get("category", "") == category]
        if category_pool:
            pool = category_pool

    # For alternatives, drop brand constraint but keep size and qualifiers
    alt_constraints = None
    if constraints:
        alt_constraints = {
            "brand": None,
            "size": constraints.get("size"),
            "qualifiers": constraints.get("qualifiers", []),
        }

    candidates = _semantic_candidates(query, pool, top_k * 3)
    if not candidates:
        candidates = _name_candidates(query, pool)
        if not candidates:
            return []

    if alt_constraints:
        candidates = _apply_hard_constraints(candidates, alt_constraints)

    ranked = rank_candidates(candidates, query)
    return ranked[:top_k] if ranked else []


def build_clarification_candidates(
    candidates: list[dict],
    max_options: int = 4,
) -> list[dict]:
    """
    Given a pre-ranked candidate list, return a structured option list for the
    clarification modal if the candidates are materially different, or [] if the
    top result should be picked silently.

    Ambiguity is triggered by:
      - Brand mismatch (any 2 candidates have different brands)
      - Size delta > 20% (same unit class)
      - Price delta > 15%

    Each returned option dict matches the frontend modal contract:
        { "id": str, "label": str, "product": dict }
    """
    pool = [p for p in candidates if p.get("available_quantity", 1) != 0][:max_options]
    if len(pool) < 2:
        return []
    if not _candidates_are_ambiguous(pool):
        return []
    return [
        {
            "id": p["id"],
            "label": " ".join(filter(None, [
                p.get("name", ""),
                p.get("package_size", ""),
            ])),
            "product": p,
        }
        for p in pool
    ]


def generate_monthly_basket_candidates(
    prefs: dict,
    order_history: list[list[dict]],
    catalog: list[dict],
    budget: float,
) -> dict:
    """
    Rule-based monthly basket generator — no LLM involved.
    The LLM presents the result; it does not compute it.

    Returns {"candidates": list[dict], "budget_overflow": bool}.
    budget_overflow=True when must_have items cost more than budget.

    Algorithm (three passes):
      1. Must-haves from prefs["must_haves"]  → tag "must_have"  (always included)
      2. Items appearing in ≥2 past orders    → tag "recurring"  (budget-capped)
      3. Highest-discount in-stock items      → tag "offer"      (fills remaining budget)

    Each item in order_history must be a list of dicts with a "product_id" key.

    Returns list[dict] with shape:
        {query, tag, status, product|None, quantity, estimated_price}
    """
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    running_cost = 0.0

    def _add(query: str, tag: str, verdict: dict) -> bool:
        """Append a resolved verdict to candidates. Returns True if added."""
        nonlocal running_cost
        product = verdict.get("product")
        qty = verdict.get("quantity", 1)
        price = (product["price"] if product else 0.0) * qty

        if product:
            if product["id"] in seen_ids:
                return False
            seen_ids.add(product["id"])

        # Must-haves always included; others are budget-gated
        if tag != "must_have" and running_cost + price > budget:
            return False

        running_cost += price
        candidates.append({
            "query": query,
            "tag": tag,
            "status": verdict["status"],
            "product": product,
            "quantity": qty,
            "estimated_price": price,
        })
        return True

    # Pass 1 — must-haves
    for query in prefs.get("must_haves", []):
        verdict = resolve_product(query, 1, catalog)
        _add(query, "must_have", verdict)

    # Pass 2 — recurring (appear in ≥2 orders)
    freq: Counter = Counter()
    for order in order_history:
        for item in order:
            pid = item.get("product_id") or item.get("id")
            if pid:
                freq[pid] += 1

    id_to_product = {p["id"]: p for p in catalog}
    for pid, count in freq.most_common():
        if count < 2:
            break
        if pid in seen_ids:
            continue
        product = id_to_product.get(pid)
        if not product or product.get("available_quantity", 1) == 0:
            continue
        _add(product.get("name", pid), "recurring", {
            "status": "resolved",
            "product": product,
            "quantity": 1,
        })

    # Pass 3 — offer fill (best discounts within remaining budget)
    if running_cost < budget:
        discounted = sorted(
            [
                p for p in catalog
                if p.get("available_quantity", 1) != 0
                and p["id"] not in seen_ids
                and p.get("discount_pct", 0) > 0
            ],
            key=lambda p: -p.get("discount_pct", 0),
        )
        for product in discounted:
            _add(product.get("name", ""), "offer", {
                "status": "resolved",
                "product": product,
                "quantity": 1,
            })

    return {"candidates": candidates, "budget_overflow": running_cost > budget}


def build_index(catalog: list[dict]) -> None:
    """
    Build and persist a semantic index from the catalog.
    Generates one embedding per product using a local sentence-transformers model
    (no API key required) and writes data/product_semantic_index.json.

    Model: paraphrase-multilingual-MiniLM-L12-v2
      - Multilingual — works well with Spanish product names
      - 384-dimensional embeddings
      - Downloaded automatically from HuggingFace on first run (~450 MB)

    search() loads this index and re-ranks by cosine similarity.
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
        "version": "current",
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
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


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


def _strip_constraints(query: str, constraints: dict) -> str:
    """
    Remove brand, size expression, and qualifier tokens from query,
    leaving only the pure product intent.

    Example:
        query="leche La Serenísima entera 1L"
        constraints={brand:"La Serenísima", size:(1000,"liquid"), qualifiers:["entera"]}
        → "leche"
    """
    result = query

    # Remove brand (accent-insensitive)
    brand = constraints.get("brand", "")
    if brand:
        norm_result = _normalize_text(result)
        norm_brand = _normalize_text(brand)
        # Find the brand span in the normalized string, then remove corresponding chars
        idx = norm_result.find(norm_brand)
        if idx != -1:
            result = result[:idx] + result[idx + len(brand):]

    # Remove size expression via _UNIT_RE
    result = _UNIT_RE.sub("", result)

    # Remove qualifier tokens (accent-insensitive)
    for qual in constraints.get("qualifiers", []):
        norm_qual = _normalize_text(qual)
        # Remove each qualifier token from result (accent-insensitive, word boundary)
        tokens = result.split()
        tokens = [t for t in tokens if _normalize_text(t) != norm_qual]
        result = " ".join(tokens)

    # Strip extra whitespace
    return " ".join(result.split()).strip()


def _name_candidates(query: str, catalog: list[dict]) -> list[dict]:
    """
    Return products whose **name** field contains all query tokens.
    Handles negation: a token preceded by "sin" in the product name does not count.
    Excludes out-of-stock products.
    Uses accent-insensitive matching via _normalize_text.
    """
    query_tokens = _tokenize(_normalize_text(query))
    if not query_tokens:
        return []

    results = []
    for p in catalog:
        if p.get("available_quantity", 1) == 0:
            continue

        name_raw = p.get("name", "")
        name_norm = _normalize_text(name_raw)
        name_tokens = _tokenize(name_norm)

        all_match = True
        for qt in query_tokens:
            # Check if this token appears in the name
            found = False
            for i, nt in enumerate(name_tokens):
                if qt in nt:
                    # Check negation: if preceded by "sin", this doesn't count
                    if i > 0 and name_tokens[i - 1] == "sin":
                        continue
                    found = True
                    break
            if not found:
                all_match = False
                break

        if all_match:
            results.append(p)

    return results


def _semantic_rerank(query: str, candidates: list[dict]) -> list[dict]:
    """
    Re-rank candidates by cosine similarity to the query embedding.
    Candidates without an embedding in the index are kept but sorted to the end.
    Returns candidates sorted by descending similarity.
    """
    try:
        model = _load_model()
    except (ImportError, Exception):
        return candidates

    entries = _load_index()
    if not entries:
        return candidates

    # Build embedding lookup by product id
    id_to_embedding = {e["id"]: e["embedding"] for e in entries}

    query_vec = model.encode([query], convert_to_numpy=True)[0].tolist()

    scored = []
    unscored = []
    for p in candidates:
        emb = id_to_embedding.get(p["id"])
        if emb is not None:
            sim = _cosine(query_vec, emb)
            scored.append((sim, p))
        else:
            unscored.append(p)

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored] + unscored


def _semantic_candidates(query: str, catalog: list[dict], top_k: int, floor: float = _SIMILARITY_FLOOR) -> list[dict]:
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
    scored = [(s, p) for s, p in scored if s >= floor]
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


def _candidates_are_ambiguous(candidates: list[dict]) -> bool:
    """
    Return True if the candidate list is ambiguous enough to require user clarification.
    Triggers on: brand mismatch, size delta > 20% (same unit class), price delta > 15%.
    """
    # Brand mismatch — any two candidates with different brands
    brands = {_normalize_text(p.get("brand", "")) for p in candidates if p.get("brand")}
    if len(brands) > 1:
        return True

    # Size delta > 20% within the same unit class
    sizes = [_normalize_size(p.get("package_size", "")) for p in candidates]
    for unit_class in ("liquid", "solid"):
        vals = [v for s in sizes if s is not None for v, u in [s] if u == unit_class]
        if len(vals) >= 2 and (max(vals) - min(vals)) / max(vals) > 0.20:
            return True

    # Price delta > 15%
    prices = [p.get("price", 0) for p in candidates if (p.get("price") or 0) > 0]
    if len(prices) >= 2 and (max(prices) - min(prices)) / max(prices) > 0.15:
        return True

    return False
