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
    parse_quantity,
    resolve_product,
    generate_monthly_basket_candidates,
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


# ─── parse_quantity() ─────────────────────────────────────────────────────────

class TestParseQuantity:
    def test_digit_quantity(self):
        assert parse_quantity("agrega 3 yogures") == 3

    def test_digit_with_container_word(self):
        assert parse_quantity("quiero 2 botellas de agua") == 2

    def test_spanish_word_dos(self):
        assert parse_quantity("dos paquetes de fideos") == 2

    def test_spanish_word_tres(self):
        assert parse_quantity("tres leches") == 3

    def test_spanish_word_un(self):
        assert parse_quantity("un yogur") == 1

    def test_size_not_confused_with_quantity(self):
        """'1L' must NOT be treated as quantity 1 when a real qty is present."""
        assert parse_quantity("3 botellas de leche 1L") == 3

    def test_size_only_returns_default(self):
        """A message with only a size expression (no quantity) returns 1."""
        assert parse_quantity("leche entera 1L") == 1

    def test_no_quantity_returns_default(self):
        assert parse_quantity("quiero leche entera") == 1

    def test_500ml_not_confused(self):
        assert parse_quantity("dame 4 jugos de 500ml") == 4

    def test_quantity_zero_ignored(self):
        """'0 unidades' makes no sense — should not return 0."""
        # After stripping size expressions, "0" remains but \b([1-9]\d*)\b won't match it
        result = parse_quantity("0 unidades de leche")
        assert result >= 1


# ─── resolve_product() ───────────────────────────────────────────────────────

class TestResolveProduct:
    def test_resolved_single_clear_result(self, monkeypatch):
        """Single unambiguous match → status resolved, substituted=False."""
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", mod.ROOT / "data" / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        # p5 is the only yogur in CATALOG — search returns it alone → no ambiguity
        result = resolve_product("yogur danone", 2, CATALOG)
        assert result["status"] == "resolved"
        assert result["product"]["id"] == "p5"
        assert result["quantity"] == 2
        assert result["substituted"] is False

    def test_needs_clarification_brand_mismatch(self, monkeypatch):
        """Multiple brands in results → needs_clarification."""
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", mod.ROOT / "data" / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        # "leche" matches p1 (Serenísima), p2 (SanCor), p3 (Serenísima) → brand mismatch
        result = resolve_product("leche entera 1L", 1, CATALOG)
        assert result["status"] == "needs_clarification"
        assert len(result["options"]) >= 2
        assert result["quantity"] == 1

    def test_not_found_all_oos(self, monkeypatch):
        """All-OOS catalog → not_found."""
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", mod.ROOT / "data" / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        oos_catalog = [
            _product("oos1", "Leche Entera SanCor", "SanCor", "1L",
                     available_quantity=0),
        ]
        result = resolve_product("leche", 1, oos_catalog)
        assert result["status"] == "not_found"
        assert result["quantity"] == 1

    def test_substituted_when_search_empty(self, monkeypatch):
        """No keyword match → find_alternatives picks something → substituted=True."""
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", mod.ROOT / "data" / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        # "vino tinto malbec" has no keyword overlap with CATALOG
        # find_alternatives last-resort fallback returns in-stock items
        result = resolve_product("vino tinto malbec", 1, CATALOG)
        assert result["status"] == "resolved"
        assert result["substituted"] is True
        assert result["product"] is not None

    def test_verdict_contains_quantity(self, monkeypatch):
        """quantity is always echoed back in the verdict."""
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", mod.ROOT / "data" / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})
        result = resolve_product("yogur danone", 5, CATALOG)
        assert result["quantity"] == 5


# ─── generate_monthly_basket_candidates() ────────────────────────────────────

class TestGenerateMonthlyBasket:
    def _no_index(self, monkeypatch):
        import backend.product_semantic_index as mod
        monkeypatch.setattr(mod, "INDEX_PATH", mod.ROOT / "data" / "nonexistent.json")
        monkeypatch.setattr(mod, "_INDEX_CACHE", {})

    def test_must_haves_tagged(self, monkeypatch):
        self._no_index(monkeypatch)
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=[],
            catalog=CATALOG,
            budget=500.0,
        )
        tags = [r["tag"] for r in result["candidates"]]
        assert "must_have" in tags

    def test_must_have_product_resolved(self, monkeypatch):
        self._no_index(monkeypatch)
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=[],
            catalog=CATALOG,
            budget=500.0,
        )
        must_haves = [r for r in result["candidates"] if r["tag"] == "must_have"]
        assert len(must_haves) == 1
        assert must_haves[0]["product"]["id"] == "p5"

    def test_recurring_items_tagged(self, monkeypatch):
        self._no_index(monkeypatch)
        # p1 appears in 3 out of 3 orders → should be tagged recurring
        history = [
            [{"product_id": "p1", "quantity": 1}],
            [{"product_id": "p1", "quantity": 1}],
            [{"product_id": "p1", "quantity": 1}],
        ]
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": []},
            order_history=history,
            catalog=CATALOG,
            budget=1000.0,
        )
        recurring = [r for r in result["candidates"] if r["tag"] == "recurring"]
        assert any(r["product"]["id"] == "p1" for r in recurring)

    def test_single_order_not_recurring(self, monkeypatch):
        """Item appearing in only 1 order must NOT be tagged recurring."""
        self._no_index(monkeypatch)
        history = [[{"product_id": "p2", "quantity": 1}]]
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": []},
            order_history=history,
            catalog=CATALOG,
            budget=1000.0,
        )
        recurring_ids = [r["product"]["id"] for r in result["candidates"] if r["tag"] == "recurring"]
        assert "p2" not in recurring_ids

    def test_budget_cap_respected(self, monkeypatch):
        self._no_index(monkeypatch)
        catalog_with_discount = CATALOG + [
            _product("d1", "Aceite Girasol Natura", "Natura", "900ml",
                     price=200.0, discount_pct=30.0),
            _product("d2", "Arroz Gallo Oro", "Gallo Oro", "1kg",
                     price=150.0, discount_pct=25.0, category="Almacén"),
        ]
        # budget of 160 — only the yogur (150) fits in must-haves; offers don't fit
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=[],
            catalog=catalog_with_discount,
            budget=160.0,
        )
        total = sum(r["estimated_price"] for r in result["candidates"] if r["tag"] != "must_have")
        assert total <= 160.0

    def test_no_duplicate_products(self, monkeypatch):
        """Same product in must_haves and order_history must appear only once."""
        self._no_index(monkeypatch)
        history = [
            [{"product_id": "p5", "quantity": 1}],
            [{"product_id": "p5", "quantity": 1}],
        ]
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=history,
            catalog=CATALOG,
            budget=1000.0,
        )
        product_ids = [r["product"]["id"] for r in result["candidates"] if r["product"]]
        assert len(product_ids) == len(set(product_ids)), "Duplicate product IDs found"

    def test_offer_tag_for_discounted_items(self, monkeypatch):
        self._no_index(monkeypatch)
        catalog_with_discount = CATALOG + [
            _product("offer1", "Aceite Girasol Natura", "Natura", "900ml",
                     price=100.0, discount_pct=40.0, category="Aceites"),
        ]
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": []},
            order_history=[],
            catalog=catalog_with_discount,
            budget=500.0,
        )
        offer_ids = [r["product"]["id"] for r in result["candidates"] if r["tag"] == "offer"]
        assert "offer1" in offer_ids

    # ── Bug #7 — budget_overflow signal ──────────────────────────────────────

    def test_budget_overflow_dict_shape(self, monkeypatch):
        """generate_monthly_basket_candidates must return a dict with 'candidates' and
        'budget_overflow' keys, not a plain list."""
        self._no_index(monkeypatch)
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=[],
            catalog=CATALOG,
            budget=500.0,
        )
        assert isinstance(result, dict), "return value must be a dict"
        assert "candidates" in result
        assert "budget_overflow" in result

    def test_budget_overflow_true_when_must_haves_exceed_budget(self, monkeypatch):
        """budget_overflow=True when must_have items cost more than the budget."""
        self._no_index(monkeypatch)
        # p5 (Yogur Danone) costs 150.0; budget=100 → must_have exceeds budget → overflow
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=[],
            catalog=CATALOG,
            budget=100.0,
        )
        assert result["budget_overflow"] is True

    def test_budget_overflow_false_when_within_budget(self, monkeypatch):
        """budget_overflow=False when all items fit within budget."""
        self._no_index(monkeypatch)
        result = generate_monthly_basket_candidates(
            prefs={"must_haves": ["yogur danone"]},
            order_history=[],
            catalog=CATALOG,
            budget=500.0,
        )
        assert result["budget_overflow"] is False
