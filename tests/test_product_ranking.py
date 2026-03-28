"""
Unit tests for product ranking and search logic.
Owner: Juan

Tests cover:
- Exact brand match ranking
- Exact package-size match ranking
- Accent-insensitive brand matching
- Unit normalisation (1L == 1000ml)
- Out-of-stock filtering in both search() and rank_candidates()
- No-match returns empty list
"""

import pytest
from backend.product_semantic_index import (
    search,
    rank_candidates,
    find_alternatives,
    _normalize_text,
    _normalize_size,
    _brand_score,
    _size_score,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _product(id, name, brand, package_size, price=100.0, available_quantity=10, category="Lácteos", discount_pct=0.0):
    return {
        "id": id,
        "name": name,
        "brand": brand,
        "package_size": package_size,
        "price": price,
        "available_quantity": available_quantity,
        "category": category,
        "discount_pct": discount_pct,
        "image_url": "",
    }


CATALOG = [
    _product("p1", "Leche Entera La Serenísima",   "La Serenísima", "1L",    price=300.0, available_quantity=10),
    _product("p2", "Leche Entera SanCor",           "SanCor",        "1L",    price=280.0, available_quantity=5),
    _product("p3", "Leche Entera La Serenísima",   "La Serenísima", "500ml", price=180.0, available_quantity=3),
    _product("p4", "Leche Descremada La Serenísima","La Serenísima", "1L",    price=310.0, available_quantity=0),  # OOS
    _product("p5", "Yogur Entero Danone",           "Danone",        "200g",  price=150.0, available_quantity=8),
]


# ─── _normalize_text ──────────────────────────────────────────────────────────

class TestNormalizeText:
    def test_strips_accents(self):
        assert _normalize_text("Serenísima") == "serenisima"

    def test_lowercase(self):
        assert _normalize_text("LECHE") == "leche"

    def test_no_accents_unchanged(self):
        assert _normalize_text("sancor") == "sancor"


# ─── _normalize_size ─────────────────────────────────────────────────────────

class TestNormalizeSize:
    def test_1L(self):
        assert _normalize_size("1L") == (1000.0, "liquid")

    def test_1000ml_equals_1L(self):
        assert _normalize_size("1000ml") == (1000.0, "liquid")

    def test_500ml(self):
        assert _normalize_size("500ml") == (500.0, "liquid")

    def test_1_litro(self):
        assert _normalize_size("1 litro") == (1000.0, "liquid")

    def test_200g(self):
        assert _normalize_size("200g") == (200.0, "solid")

    def test_1kg(self):
        assert _normalize_size("1kg") == (1000.0, "solid")

    def test_no_size_returns_none(self):
        assert _normalize_size("leche entera") is None

    def test_size_embedded_in_query(self):
        assert _normalize_size("quiero leche entera 1L por favor") == (1000.0, "liquid")


# ─── search() ────────────────────────────────────────────────────────────────

class TestSearch:
    def test_out_of_stock_excluded(self):
        results = search("leche descremada", CATALOG)
        ids = [p["id"] for p in results]
        assert "p4" not in ids, "OOS product p4 must not appear in search results"

    def test_no_match_returns_empty(self):
        results = search("vino tinto malbec", CATALOG)
        assert results == []

    def test_top_k_respected(self):
        results = search("leche", CATALOG, top_k=2)
        assert len(results) <= 2

    def test_returns_relevant_products(self):
        results = search("yogur", CATALOG)
        ids = [p["id"] for p in results]
        assert "p5" in ids


# ─── rank_candidates() ───────────────────────────────────────────────────────

class TestRankCandidates:
    def test_exact_brand_ranks_first(self):
        """'leche serenisima 1L' → p1 (brand + size match) before p2 (size only)."""
        results = rank_candidates(CATALOG, "leche serenisima 1L")
        ids = [p["id"] for p in results]
        assert ids[0] == "p1", f"Expected p1 first, got {ids}"

    def test_competing_brand_wins(self):
        """'leche sancor 1L' → p2 (SanCor brand) should rank above p1."""
        results = rank_candidates(CATALOG, "leche sancor 1L")
        ids = [p["id"] for p in results]
        assert ids.index("p2") < ids.index("p1"), (
            f"p2 (SanCor) should rank above p1 for query 'leche sancor 1L', got order {ids}"
        )

    def test_package_size_exact_match(self):
        """'leche entera 500ml' → p3 (500ml) should rank above p1 (1L)."""
        results = rank_candidates(CATALOG, "leche entera 500ml")
        ids = [p["id"] for p in results]
        assert ids.index("p3") < ids.index("p1"), (
            f"p3 (500ml) should rank above p1 (1L) for '500ml' query, got {ids}"
        )

    def test_out_of_stock_excluded(self):
        """rank_candidates must exclude OOS items even when passed directly."""
        results = rank_candidates(CATALOG, "leche descremada")
        ids = [p["id"] for p in results]
        assert "p4" not in ids, "OOS product p4 must be excluded from rank_candidates"

    def test_accent_insensitive_brand_match(self):
        """'leche serenisima' (no accent) should still match La Serenísima products."""
        results = rank_candidates(CATALOG, "leche serenisima")
        ids = [p["id"] for p in results]
        assert "p1" in ids or "p3" in ids, (
            "La Serenísima products must match accent-insensitive query 'serenisima'"
        )

    def test_size_normalisation_1L_vs_1000ml(self):
        """'leche 1000ml' should match p1 (1L) via unit normalisation."""
        results = rank_candidates(CATALOG, "leche 1000ml")
        ids = [p["id"] for p in results]
        # p1 has 1L == 1000ml, p3 has 500ml — p1 should rank above p3
        assert "p1" in ids, "p1 (1L) must appear for '1000ml' query"
        assert ids.index("p1") < ids.index("p3"), (
            f"p1 (1L=1000ml) should rank above p3 (500ml) for '1000ml' query, got {ids}"
        )

    def test_no_match_returns_empty(self):
        results = rank_candidates(CATALOG, "vino tinto")
        assert results == []


# ─── search() semantic fallback ───────────────────────────────────────────────

class TestSearchSemanticFallback:
    def test_keyword_fallback_when_no_index(self, tmp_path, monkeypatch):
        """With no index file on disk, search() must still return keyword results."""
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", tmp_path / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        results = search("leche", CATALOG)
        ids = [p["id"] for p in results]
        assert "p1" in ids or "p2" in ids or "p3" in ids

    def test_semantic_search_broad_query(self, tmp_path, monkeypatch):
        """
        With a live semantic index, a broad query like 'para el desayuno' should
        return at least one dairy/breakfast product.
        Skipped if sentence-transformers is not installed.
        """
        st = pytest.importorskip("sentence_transformers")
        import backend.product_semantic_index as mod
        from backend.product_semantic_index import build_index

        index_file = tmp_path / "index.json"
        monkeypatch.setattr(mod, "INDEX_PATH", index_file)
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        monkeypatch.setattr(mod, "_MODEL_CACHE", {})

        build_index(CATALOG)
        results = search("para el desayuno", CATALOG)
        assert len(results) >= 1, "Broad query should return at least one result via semantic search"


# ─── find_alternatives() ─────────────────────────────────────────────────────

class TestFindAlternatives:
    def test_excludes_oos(self):
        """find_alternatives must never return out-of-stock items."""
        results = find_alternatives("leche descremada", CATALOG)
        ids = [p["id"] for p in results]
        assert "p4" not in ids, "OOS product p4 must not appear in alternatives"

    def test_same_category_filter(self):
        """When category='Lácteos' all results must be from that category."""
        results = find_alternatives("producto", CATALOG, category="Lácteos")
        for p in results:
            assert p["category"] == "Lácteos"

    def test_falls_back_cross_category(self):
        """If same-category pool is empty, return results from the full catalog."""
        results = find_alternatives("algo", CATALOG, category="Bebidas Alcohólicas")
        # No products in 'Bebidas Alcohólicas' in CATALOG, so should fall back
        assert len(results) >= 1, "Should return cross-category fallback results"

    def test_returns_only_in_stock(self):
        """Result list must only contain products with available_quantity > 0."""
        results = find_alternatives("leche", CATALOG)
        for p in results:
            assert p.get("available_quantity", 1) > 0
