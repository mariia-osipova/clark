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
    build_clarification_candidates,
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
        assert index_file.exists(), "build_index() must write the index file"
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
        """When category='Lácteos', all results must be from that category.
        Query 'yogur' has a Lácteos match (p5) and no matches in other categories,
        so if filtering is broken and the full catalog is searched, we would still
        get Lácteos results. Use a non-empty query to ensure filtering is exercised."""
        results = find_alternatives("leche entera", CATALOG, category="Lácteos")
        assert len(results) >= 1, "Should return at least one Lácteos result"
        for p in results:
            assert p["category"] == "Lácteos", f"Expected Lácteos, got {p['category']}"

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


# ─── rank_candidates() — offers-aware ────────────────────────────────────────

class TestOffersRanking:
    def test_high_discount_beats_low_discount(self):
        """40% OFF product should rank above 5% OFF product when otherwise equal."""
        cheap = _product("d1", "Leche Entera Genérica", "Genérica", "1L",
                         price=100.0, discount_pct=5.0)
        deal  = _product("d2", "Leche Entera Genérica", "Genérica", "1L",
                         price=100.0, discount_pct=40.0)
        results = rank_candidates([cheap, deal], "leche entera")
        ids = [p["id"] for p in results]
        assert ids.index("d2") < ids.index("d1"), (
            f"40% OFF product (d2) should rank above 5% OFF (d1), got {ids}"
        )

    def test_discount_tiers_applied(self):
        """Products at 40%, 20%, 10%, 5% should rank in that order when keywords equal."""
        p40 = _product("t40", "Aceite Genérico", "Genérica", "1L", price=100.0, discount_pct=40.0)
        p20 = _product("t20", "Aceite Genérico", "Genérica", "1L", price=100.0, discount_pct=20.0)
        p10 = _product("t10", "Aceite Genérico", "Genérica", "1L", price=100.0, discount_pct=10.0)
        p5  = _product("t5",  "Aceite Genérico", "Genérica", "1L", price=100.0, discount_pct=5.0)
        results = rank_candidates([p5, p10, p20, p40], "aceite")
        ids = [p["id"] for p in results]
        assert ids == ["t40", "t20", "t10", "t5"], (
            f"Expected descending discount order, got {ids}"
        )

    def test_brand_plus_discount_ranks_highest(self):
        """Brand match (5.0) + 40% discount (4.0) = 9.0 should beat brand-only (5.0)."""
        brand_only = _product("b1", "Leche Entera SanCor", "SanCor", "1L",
                               price=100.0, discount_pct=0.0)
        brand_deal = _product("b2", "Leche Entera SanCor", "SanCor", "1L",
                               price=100.0, discount_pct=40.0)
        results = rank_candidates([brand_only, brand_deal], "leche sancor")
        ids = [p["id"] for p in results]
        assert ids.index("b2") < ids.index("b1"), (
            f"Brand + 40% OFF (b2) should rank above brand-only (b1), got {ids}"
        )


# ─── build_clarification_candidates() ────────────────────────────────────────

class TestClarificationCandidates:
    def test_brand_mismatch_triggers(self):
        """Two candidates with different brands → non-empty option list returned."""
        candidates = [
            _product("c1", "Leche Entera La Serenísima", "La Serenísima", "1L", price=300.0),
            _product("c2", "Leche Entera SanCor",         "SanCor",        "1L", price=280.0),
        ]
        opts = build_clarification_candidates(candidates)
        assert len(opts) == 2, f"Expected 2 options for brand mismatch, got {len(opts)}"

    def test_same_brand_size_price_no_clarification(self):
        """Same brand, same size, same price → no clarification needed."""
        candidates = [
            _product("s1", "Leche Entera SanCor", "SanCor", "1L", price=280.0),
            _product("s2", "Leche Entera SanCor", "SanCor", "1L", price=280.0),
        ]
        opts = build_clarification_candidates(candidates)
        assert opts == [], f"Expected no clarification for identical candidates, got {opts}"

    def test_size_delta_over_20pct_triggers(self):
        """500ml vs 1000ml = 50% size delta → triggers clarification."""
        candidates = [
            _product("z1", "Leche Entera SanCor", "SanCor", "500ml",  price=180.0),
            _product("z2", "Leche Entera SanCor", "SanCor", "1000ml", price=300.0),
        ]
        opts = build_clarification_candidates(candidates)
        assert len(opts) >= 2, f"Expected clarification for large size delta, got {opts}"

    def test_price_delta_over_15pct_triggers(self):
        """$100 vs $120 = 20% price delta → triggers clarification."""
        candidates = [
            _product("pr1", "Aceite SanCor", "SanCor", "1L", price=100.0),
            _product("pr2", "Aceite SanCor", "SanCor", "1L", price=120.0),
        ]
        opts = build_clarification_candidates(candidates)
        assert len(opts) >= 2, f"Expected clarification for price delta >15%, got {opts}"

    def test_single_candidate_no_clarification(self):
        """Only one in-stock result → never ask for clarification."""
        candidates = [
            _product("one", "Leche Entera SanCor", "SanCor", "1L", price=280.0),
        ]
        opts = build_clarification_candidates(candidates)
        assert opts == [], "Single candidate must not trigger clarification"

    def test_option_dict_shape(self):
        """Each returned option must have id, label, and product keys."""
        candidates = [
            _product("x1", "Leche Entera La Serenísima", "La Serenísima", "1L",  price=300.0),
            _product("x2", "Leche Entera SanCor",         "SanCor",        "1L",  price=280.0),
        ]
        opts = build_clarification_candidates(candidates)
        assert len(opts) == 2
        for opt in opts:
            assert "id" in opt, "Option must have 'id'"
            assert "label" in opt, "Option must have 'label'"
            assert "product" in opt, "Option must have 'product'"
            assert "price" in opt["product"], "product must contain 'price'"
            assert "brand" in opt["product"], "product must contain 'brand'"
