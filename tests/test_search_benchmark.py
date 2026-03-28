"""
Semantic search benchmark.

Run:  pytest tests/test_search_benchmark.py -v
Requires OPENAI_API_KEY in environment.
If key is absent, all tests are skipped.
"""
import os
from pathlib import Path
from dataclasses import dataclass, field

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.product_semantic_index import build_index, search

# ── Fixture catalog ───────────────────────────────────────────────────────────
# 30 products spanning 10 categories. Two products are OOS (available_quantity=0).

FIXTURE_CATALOG = [
    # Lácteos
    {"id": "L001", "name": "Leche entera La Serenísima 1L",       "brand": "La Serenísima", "package_size": "1 L",          "price": 350.0, "list_price": 350.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Lácteos",    "image_url": ""},
    {"id": "L002", "name": "Leche descremada La Serenísima 1L",   "brand": "La Serenísima", "package_size": "1 L",          "price": 360.0, "list_price": 360.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Lácteos",    "image_url": ""},
    {"id": "L003", "name": "Leche sin lactosa La Serenísima 1L",  "brand": "La Serenísima", "package_size": "1 L",          "price": 420.0, "list_price": 420.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Lácteos",    "image_url": ""},
    {"id": "L004", "name": "Leche entera Ilolay 1L",              "brand": "Ilolay",        "package_size": "1 L",          "price": 330.0, "list_price": 330.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Lácteos",    "image_url": ""},
    {"id": "L005", "name": "Yogur entero frutilla Danone 200g",   "brand": "Danone",        "package_size": "200 g",        "price": 180.0, "list_price": 180.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Lácteos",    "image_url": ""},
    {"id": "L006", "name": "Crema de leche La Serenísima 200ml",  "brand": "La Serenísima", "package_size": "200 ml",       "price": 220.0, "list_price": 220.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Lácteos",    "image_url": ""},
    # Harinas / Panificación
    {"id": "H001", "name": "Harina 000 Pureza 1kg",               "brand": "Pureza",        "package_size": "1 kg",         "price": 280.0, "list_price": 280.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Harinas",    "image_url": ""},
    {"id": "H002", "name": "Harina integral Pureza 1kg",          "brand": "Pureza",        "package_size": "1 kg",         "price": 300.0, "list_price": 300.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Harinas",    "image_url": ""},
    {"id": "H003", "name": "Azúcar común Ledesma 1kg",            "brand": "Ledesma",       "package_size": "1 kg",         "price": 250.0, "list_price": 250.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Azúcar",     "image_url": ""},
    {"id": "H004", "name": "Azúcar impalpable Ledesma 500g",      "brand": "Ledesma",       "package_size": "500 g",        "price": 180.0, "list_price": 180.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Azúcar",     "image_url": ""},
    {"id": "H005", "name": "Huevos blancos docena",               "brand": "",              "package_size": "12 unidades",  "price": 650.0, "list_price": 650.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Huevos",     "image_url": ""},
    {"id": "H006", "name": "Levadura Fleischmann 10g",            "brand": "Fleischmann",   "package_size": "10 g",         "price":  80.0, "list_price":  80.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Harinas",    "image_url": ""},
    # Aceites
    {"id": "A001", "name": "Aceite de girasol Cocinero 1.5L",     "brand": "Cocinero",      "package_size": "1.5 L",        "price": 600.0, "list_price": 600.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Aceites",    "image_url": ""},
    {"id": "A002", "name": "Aceite de oliva La Española 500ml",   "brand": "La Española",   "package_size": "500 ml",       "price":1200.0, "list_price":1200.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Aceites",    "image_url": ""},
    # Bebidas
    {"id": "B001", "name": "Agua mineral Villavicencio 1.5L",     "brand": "Villavicencio", "package_size": "1.5 L",        "price": 180.0, "list_price": 180.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Aguas",      "image_url": ""},
    {"id": "B002", "name": "Coca-Cola 2.25L",                     "brand": "Coca-Cola",     "package_size": "2.25 L",       "price": 400.0, "list_price": 400.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Bebidas",    "image_url": ""},
    {"id": "B003", "name": "Cerveza Quilmes 1L",                  "brand": "Quilmes",       "package_size": "1 L",          "price": 350.0, "list_price": 350.0, "discount_pct": 0.0,  "available_quantity":  0, "category": "Bebidas",    "image_url": ""},  # OOS
    # Pastas / Cereales
    {"id": "P001", "name": "Fideos spaghetti Don Vicente 500g",   "brand": "Don Vicente",   "package_size": "500 g",        "price": 200.0, "list_price": 200.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Pastas",     "image_url": ""},
    {"id": "P002", "name": "Arroz doble carolina Gallo 1kg",      "brand": "Gallo",         "package_size": "1 kg",         "price": 350.0, "list_price": 350.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Arroces",    "image_url": ""},
    {"id": "P003", "name": "Avena instantánea Quaker 500g",       "brand": "Quaker",        "package_size": "500 g",        "price": 280.0, "list_price": 280.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Cereales",   "image_url": ""},
    # Café / Té / Mate
    {"id": "C001", "name": "Café molido Cabrales 250g",           "brand": "Cabrales",      "package_size": "250 g",        "price": 500.0, "list_price": 500.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Cafés",      "image_url": ""},
    {"id": "C002", "name": "Yerba mate Taragüí 500g",             "brand": "Taragüí",       "package_size": "500 g",        "price": 400.0, "list_price": 400.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Yerba mate", "image_url": ""},
    {"id": "C003", "name": "Té Lipton 25 saquitos",               "brand": "Lipton",        "package_size": "25 unidades",  "price": 250.0, "list_price": 250.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Tés",        "image_url": ""},
    # Conservas
    {"id": "K001", "name": "Tomate triturado Arcor 400g",         "brand": "Arcor",         "package_size": "400 g",        "price": 150.0, "list_price": 150.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Conservas",  "image_url": ""},
    {"id": "K002", "name": "Atún al natural La Campagnola 170g",  "brand": "La Campagnola", "package_size": "170 g",        "price": 350.0, "list_price": 350.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Conservas",  "image_url": ""},
    # Limpieza
    {"id": "I001", "name": "Detergente lavavajillas Magistral 750ml", "brand": "Magistral", "package_size": "750 ml",       "price": 300.0, "list_price": 300.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Limpieza",   "image_url": ""},
    {"id": "I002", "name": "Lavandina Ayudín 1L",                 "brand": "Ayudín",        "package_size": "1 L",          "price": 180.0, "list_price": 180.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Limpieza",   "image_url": ""},
    {"id": "I003", "name": "Jabón en polvo Skip 1kg",             "brand": "Skip",          "package_size": "1 kg",         "price": 600.0, "list_price": 600.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Limpieza",   "image_url": ""},
    # Dulces
    {"id": "D001", "name": "Dulce de leche Sancor 400g",          "brand": "Sancor",        "package_size": "400 g",        "price": 450.0, "list_price": 450.0, "discount_pct": 0.0,  "available_quantity": 99, "category": "Dulces",     "image_url": ""},
    {"id": "D002", "name": "Chocolate Milka 150g",                "brand": "Milka",         "package_size": "150 g",        "price": 380.0, "list_price": 380.0, "discount_pct": 0.0,  "available_quantity":  0, "category": "Golosinas",  "image_url": ""},  # OOS
]


# ── Test-case dataclass ────────────────────────────────────────────────────────

@dataclass
class SearchCase:
    id: str
    query: str
    tier: str          # lexical | semantic_synonym | semantic_descriptive | constraint | oos | noise
    expected_ids: list  # product ids that satisfy the query; empty for noise
    match: str         # top1 | top3 | any_in_top3 | not_in_results | empty
    constraints: dict = field(default_factory=dict)
    note: str = ""


# ── Session fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def fixture_index_path(tmp_path_factory):
    """Build OpenAI index for FIXTURE_CATALOG once per test session. Skips if no API key."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key or key.startswith("sk-test"):
        pytest.skip("Real OPENAI_API_KEY required — skipping semantic benchmark")
    out = tmp_path_factory.mktemp("idx") / "fixture_index.json"
    build_index(FIXTURE_CATALOG, out_path=out)
    return out


@pytest.fixture()
def patched_index(fixture_index_path, monkeypatch):
    """Point the module's INDEX_PATH to the fixture index and clear caches."""
    import backend.product_semantic_index as mod
    mod._INDEX_CACHE.clear()
    mod._QUERY_EMBED_CACHE.clear()
    monkeypatch.setattr(mod, "INDEX_PATH", fixture_index_path)


# ── Test cases ─────────────────────────────────────────────────────────────────
#
# Tiers:
#   lexical            — query closely names the product (tests exact/near match)
#   semantic_synonym   — query uses a synonym not present in any product name
#   semantic_descriptive — query describes a use/need, not a product name
#   constraint         — query includes a qualifier that must select the right variant
#   oos                — target product is OOS; it must NOT appear in results
#   noise              — query is entirely outside catalog domain; results must be empty

SEARCH_CASES = [
    # ── Lexical (8 cases) ─────────────────────────────────────────────────────
    SearchCase("L01", "leche entera La Serenísima",
               "lexical", ["L001"], "top1",
               note="Brand + qualifier exact match"),
    SearchCase("L02", "azúcar Ledesma 1kg",
               "lexical", ["H003"], "top1",
               note="Brand + size"),
    SearchCase("L03", "aceite de oliva",
               "lexical", ["A002"], "top1",
               note="Must rank olive oil above sunflower oil"),
    SearchCase("L04", "harina integral",
               "lexical", ["H002"], "top1",
               note="Qualifier 'integral' selects the right variant"),
    SearchCase("L05", "yogur frutilla Danone",
               "lexical", ["L005"], "top1",
               note="Brand + flavour"),
    SearchCase("L06", "café Cabrales",
               "lexical", ["C001"], "top1"),
    SearchCase("L07", "leche descremada",
               "lexical", ["L002"], "top1",
               note="Qualifier 'descremada' selects the right variant"),
    SearchCase("L08", "agua Villavicencio",
               "lexical", ["B001"], "top1"),

    # ── Semantic synonym (7 cases) ────────────────────────────────────────────
    # The query word does NOT appear in any product name.
    SearchCase("S01", "milk",
               "semantic_synonym", ["L001", "L002", "L003", "L004"], "any_in_top3",
               note="English word → leche"),
    SearchCase("S02", "pasta",
               "semantic_synonym", ["P001"], "top3",
               note="'pasta' → fideos spaghetti"),
    SearchCase("S03", "gaseosa",
               "semantic_synonym", ["B002"], "top3",
               note="Argentine colloquial for carbonated soft drink → Coca-Cola"),
    SearchCase("S04", "yerba",
               "semantic_synonym", ["C002"], "top1",
               note="'yerba' is the colloquial shorthand for 'yerba mate'"),
    SearchCase("S05", "sugar",
               "semantic_synonym", ["H003", "H004"], "any_in_top3",
               note="English word → azúcar"),
    SearchCase("S06", "bleach",
               "semantic_synonym", ["I002"], "top3",
               note="English word → lavandina"),
    SearchCase("S07", "detergente para platos",
               "semantic_synonym", ["I001"], "top3",
               note="'para platos' is not in the product name 'lavavajillas'"),

    # ── Semantic descriptive (8 cases) ────────────────────────────────────────
    # Query describes a need or use case; no product name is mentioned.
    SearchCase("D01", "algo para tomar mate",
               "semantic_descriptive", ["C002"], "top3",
               note="'tomar mate' → yerba mate"),
    SearchCase("D02", "para hacer una torta",
               "semantic_descriptive", ["H001", "H002", "H003", "H005"], "any_in_top3",
               note="Baking ingredients: harina, azúcar, huevos"),
    SearchCase("D03", "para el desayuno con leche",
               "semantic_descriptive", ["L001", "L002", "L003", "L004", "P003", "C001"], "any_in_top3",
               note="Breakfast with milk: leche, avena, café"),
    SearchCase("D04", "bebida sin alcohol para los chicos",
               "semantic_descriptive", ["B001", "B002"], "any_in_top3",
               note="Non-alcoholic drink for kids → agua or Coca-Cola"),
    SearchCase("D05", "para limpiar la ropa",
               "semantic_descriptive", ["I003"], "top3",
               note="Laundry → jabón en polvo, not lavavajillas"),
    SearchCase("D06", "proteína para el desayuno",
               "semantic_descriptive", ["H005", "K002", "L005"], "any_in_top3",
               note="Protein breakfast → huevos, atún, yogur"),
    SearchCase("D07", "para condimentar la ensalada",
               "semantic_descriptive", ["A002"], "top3",
               note="Salad dressing → aceite de oliva, not girasol"),
    SearchCase("D08", "algo dulce para untar en el pan",
               "semantic_descriptive", ["D001"], "top3",
               note="Sweet spread → dulce de leche"),

    # ── Constraint (4 cases) ──────────────────────────────────────────────────
    SearchCase("C01", "leche sin lactosa",
               "constraint", ["L003"], "top1",
               note="Qualifier 'sin lactosa' must select L003 over L001/L002/L004"),
    SearchCase("C02", "harina integral",
               "constraint", ["H002"], "top1",
               note="Qualifier 'integral' must rank H002 above H001"),
    SearchCase("C03", "aceite de girasol",
               "constraint", ["A001"], "top1",
               note="Variety 'girasol' must rank A001 above olive oil A002"),
    SearchCase("C04", "leche entera",
               "constraint", ["L001", "L004"], "any_in_top3",
               note="Two brands of 'leche entera' are both acceptable"),

    # ── OOS (2 cases) ─────────────────────────────────────────────────────────
    # OOS products must never surface in results.
    SearchCase("O01", "cerveza Quilmes",
               "oos", ["B003"], "not_in_results",
               note="B003 is OOS — must be excluded from results"),
    SearchCase("O02", "chocolate Milka",
               "oos", ["D002"], "not_in_results",
               note="D002 is OOS — must be excluded from results"),

    # ── Noise (3 cases) ───────────────────────────────────────────────────────
    # Queries entirely outside the catalog domain → must return no results.
    SearchCase("N01", "televisor Samsung 55 pulgadas",
               "noise", [], "empty",
               note="Electronics not in catalog"),
    SearchCase("N02", "medicamento paracetamol 500mg",
               "noise", [], "empty",
               note="Pharmaceuticals not in catalog"),
    SearchCase("N03", "vuelos baratos a Miami",
               "noise", [], "empty",
               note="Travel not in catalog"),
]


# ── Parametrized test ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", SEARCH_CASES, ids=[c.id for c in SEARCH_CASES])
def test_benchmark(case, patched_index):
    results = search(case.query, FIXTURE_CATALOG, top_k=3,
                     constraints=case.constraints or None)
    result_ids = [r["id"] for r in results]

    if case.match == "top1":
        assert result_ids[:1] == [case.expected_ids[0]], (
            f"[{case.id}] Expected top-1={case.expected_ids[0]}, got {result_ids}  |  {case.note}"
        )
    elif case.match == "top3":
        assert case.expected_ids[0] in result_ids, (
            f"[{case.id}] Expected {case.expected_ids[0]} in top-3, got {result_ids}  |  {case.note}"
        )
    elif case.match == "any_in_top3":
        overlap = set(case.expected_ids) & set(result_ids)
        assert overlap, (
            f"[{case.id}] Expected any of {case.expected_ids} in top-3, got {result_ids}  |  {case.note}"
        )
    elif case.match == "not_in_results":
        for eid in case.expected_ids:
            assert eid not in result_ids, (
                f"[{case.id}] OOS product {eid} must not appear in results, got {result_ids}  |  {case.note}"
            )
    elif case.match == "empty":
        assert result_ids == [], (
            f"[{case.id}] Noise query must return no results, got {result_ids}  |  {case.note}"
        )
