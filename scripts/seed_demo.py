#!/usr/bin/env python3
"""
Demo seed script — sets up the playground user (Martín García) with realistic data.
Safe to re-run: all DB inserts are idempotent.

Usage:
    python3 scripts/seed_demo.py

What it does:
    1. Creates data/catalog_snapshot.json with 20 demo products (if missing)
    2. Builds data/product_semantic_index.json so product resolution works
    3. Seeds the SQLite DB: 2 past orders, preferences, recurring plan
    4. Writes data/user_profile.json with the full demo persona + cart profiles
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─── Demo catalog ─────────────────────────────────────────────────────────────

DEMO_CATALOG = [
    # Lácteos
    {
        "id": "demo_leche_serenisima",
        "name": "Leche entera La Serenísima",
        "brand": "La Serenísima",
        "package_size": "1L",
        "price": 750.0,
        "list_price": 750.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Lácteos",
    },
    {
        "id": "demo_leche_desc_serenisima",
        "name": "Leche descremada La Serenísima",
        "brand": "La Serenísima",
        "package_size": "1L",
        "price": 780.0,
        "list_price": 780.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Lácteos",
    },
    {
        "id": "demo_yogur_danone",
        "name": "Yogur frutilla Danone",
        "brand": "Danone",
        "package_size": "190g",
        "price": 420.0,
        "list_price": 420.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Lácteos",
    },
    {
        "id": "demo_manteca_pampena",
        "name": "Manteca La Pampeana",
        "brand": "La Pampeana",
        "package_size": "200g",
        "price": 680.0,
        "list_price": 680.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Lácteos",
    },
    {
        "id": "demo_queso_finlandia",
        "name": "Queso cremoso Finlandia",
        "brand": "Finlandia",
        "package_size": "300g",
        "price": 1450.0,
        "list_price": 1450.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Lácteos",
    },
    # Almacén
    {
        "id": "demo_arroz_gallo",
        "name": "Arroz largo fino Gallo",
        "brand": "Gallo",
        "package_size": "1kg",
        "price": 980.0,
        "list_price": 980.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Almacén",
    },
    {
        "id": "demo_fideos_matarazzo",
        "name": "Fideos tallarín Matarazzo",
        "brand": "Matarazzo",
        "package_size": "500g",
        "price": 760.0,
        "list_price": 760.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Almacén",
    },
    {
        "id": "demo_aceite_natura",
        "name": "Aceite girasol Natura",
        "brand": "Natura",
        "package_size": "900ml",
        "price": 1350.0,
        "list_price": 1350.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Almacén",
    },
    {
        "id": "demo_azucar_ledesma",
        "name": "Azúcar Ledesma",
        "brand": "Ledesma",
        "package_size": "1kg",
        "price": 620.0,
        "list_price": 620.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Almacén",
    },
    {
        "id": "demo_harina_morixe",
        "name": "Harina 000 Morixe",
        "brand": "Morixe",
        "package_size": "1kg",
        "price": 580.0,
        "list_price": 580.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Almacén",
    },
    {
        "id": "demo_purete_arcor",
        "name": "Puré de tomate Arcor",
        "brand": "Arcor",
        "package_size": "520g",
        "price": 490.0,
        "list_price": 490.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Almacén",
    },
    # Panadería
    {
        "id": "demo_pan_fargo",
        "name": "Pan lactal Fargo",
        "brand": "Fargo",
        "package_size": "500g",
        "price": 890.0,
        "list_price": 890.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Panadería",
    },
    {
        "id": "demo_galletitas_oreo",
        "name": "Galletitas Oreo",
        "brand": "Oreo",
        "package_size": "117g",
        "price": 650.0,
        "list_price": 650.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Panadería",
    },
    # Huevos
    {
        "id": "demo_huevos_lapalma",
        "name": "Huevos frescos La Palma",
        "brand": "La Palma",
        "package_size": "x12 L",
        "price": 1890.0,
        "list_price": 1890.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Huevos y Lácteos",
    },
    # Bebidas
    {
        "id": "demo_yerba_taragui",
        "name": "Yerba mate Taragüí",
        "brand": "Taragüí",
        "package_size": "1kg",
        "price": 1850.0,
        "list_price": 1850.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Bebidas",
    },
    {
        "id": "demo_agua_villavicencio",
        "name": "Agua mineral Villavicencio",
        "brand": "Villavicencio",
        "package_size": "2.25L",
        "price": 890.0,
        "list_price": 890.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Bebidas",
    },
    # Limpieza
    {
        "id": "demo_detergente_magistral",
        "name": "Detergente Magistral limón",
        "brand": "Magistral",
        "package_size": "500ml",
        "price": 780.0,
        "list_price": 780.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Limpieza",
    },
    {
        "id": "demo_lavandina_ayudin",
        "name": "Lavandina Ayudín concentrada",
        "brand": "Ayudín",
        "package_size": "2L",
        "price": 960.0,
        "list_price": 960.0,
        "discount_pct": 0,
        "offer_label": "",
        "image_url": "",
        "available_quantity": 50,
        "category": "Limpieza",
    },
]

# ─── Demo profile ──────────────────────────────────────────────────────────────

DEMO_PROFILE = {
    "_schema_version": 1,
    "_doc": "Playground demo user — Martín García",
    "identity": {
        "name": "Martín",
        "locale": "es-AR",
    },
    "household": {
        "size": 4,
        "has_children": True,
        "has_pets": False,
        "dietary_restrictions": [],
    },
    "preferences": {
        "preferred_brands": {"lácteos": "La Serenísima"},
        "excluded_brands": [],
        "excluded_categories": [],
        "budget_monthly": 20000,
        "strict_brand": False,
    },
    "purchase_history": {
        "frequent_items": [
            "demo_leche_serenisima",
            "demo_huevos_lapalma",
            "demo_pan_fargo",
        ],
        "last_order_date": None,
        "avg_cart_size": 8,
        "shopping_frequency_days": 7,
    },
    "interaction": {
        "auto_pick_suggestions": False,
        "verbosity": "normal",
    },
    "cart_profiles": {
        "desayuno": [
            {"product_id": "demo_leche_serenisima", "quantity": 2},
            {"product_id": "demo_yogur_danone",     "quantity": 4},
            {"product_id": "demo_pan_fargo",        "quantity": 1},
            {"product_id": "demo_manteca_pampena",  "quantity": 1},
        ],
        "despensa": [
            {"product_id": "demo_arroz_gallo",      "quantity": 2},
            {"product_id": "demo_fideos_matarazzo", "quantity": 3},
            {"product_id": "demo_aceite_natura",    "quantity": 1},
            {"product_id": "demo_purete_arcor",     "quantity": 4},
            {"product_id": "demo_harina_morixe",    "quantity": 1},
            {"product_id": "demo_azucar_ledesma",   "quantity": 1},
        ],
    },
}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _catalog_path() -> Path:
    return ROOT / "data" / "catalog_snapshot.json"


def _index_path() -> Path:
    return ROOT / "data" / "product_semantic_index.json"


def _profile_path() -> Path:
    return ROOT / "data" / "user_profile.json"


def _db_path() -> Path:
    from backend.db import get_db_path
    return get_db_path()


def _cart_item(product: dict, quantity: int) -> dict:
    return {
        "product_id": product["id"],
        "name": product["name"],
        "brand": product.get("brand", ""),
        "package_size": product.get("package_size", ""),
        "price": product["price"],
        "quantity": quantity,
        "image_url": product.get("image_url", ""),
    }


# ─── Seed functions ────────────────────────────────────────────────────────────

def seed_catalog() -> None:
    catalog_path = _catalog_path()

    if catalog_path.exists():
        print(f"  Catalog already exists ({catalog_path.name}) — skipping write.")
    else:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(DEMO_CATALOG, f, indent=2, ensure_ascii=False)
        print(f"  Catalog: {len(DEMO_CATALOG)} products written to {catalog_path.name}")

    index_path = _index_path()
    if index_path.exists():
        print(f"  Semantic index already exists ({index_path.name}) — skipping build.")
    else:
        print("  Building semantic index (downloads model on first run, ~450 MB)…")
        from backend.product_semantic_index import build_index
        catalog = json.loads(catalog_path.read_text())
        build_index(catalog)
        print(f"  Semantic index written to {index_path.name}")


def seed_db() -> None:
    from backend.db import init_db, get_db_path

    init_db()
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row

    catalog_by_id = {p["id"]: p for p in DEMO_CATALOG}

    # ── Orders ─────────────────────────────────────────────────────────────────
    order_a_items = [
        _cart_item(catalog_by_id["demo_leche_serenisima"], 2),
        _cart_item(catalog_by_id["demo_huevos_lapalma"],   1),
        _cart_item(catalog_by_id["demo_manteca_pampena"],  1),
        _cart_item(catalog_by_id["demo_yerba_taragui"],    1),
    ]
    order_a_total = round(sum(i["price"] * i["quantity"] for i in order_a_items), 2)

    order_b_items = [
        _cart_item(catalog_by_id["demo_leche_serenisima"], 2),
        _cart_item(catalog_by_id["demo_arroz_gallo"],      2),
        _cart_item(catalog_by_id["demo_fideos_matarazzo"], 2),
        _cart_item(catalog_by_id["demo_purete_arcor"],     3),
    ]
    order_b_total = round(sum(i["price"] * i["quantity"] for i in order_b_items), 2)

    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            "INSERT OR REPLACE INTO orders (id, cart_json, total, created_at) VALUES (?, ?, ?, datetime('now', '-7 days'))",
            ("demo_order_a", json.dumps(order_a_items, ensure_ascii=False), order_a_total),
        )
        conn.execute(
            "INSERT OR REPLACE INTO orders (id, cart_json, total, created_at) VALUES (?, ?, ?, datetime('now', '-30 days'))",
            ("demo_order_b", json.dumps(order_b_items, ensure_ascii=False), order_b_total),
        )
        conn.commit()
        print(f"  Orders: 2 demo orders seeded (totals ${order_a_total:.0f} / ${order_b_total:.0f})")
    except Exception:
        conn.rollback()
        raise

    # ── Preferences ────────────────────────────────────────────────────────────
    prefs = {
        "notes": "Prefiero productos La Serenísima para lácteos",
        "excluded_categories": [],
        "preferred_brands": {"lácteos": "La Serenísima"},
        "household_size": 4,
    }
    try:
        conn.execute(
            """INSERT INTO preferences (key, prefs_json, updated_at)
               VALUES ('default', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   prefs_json = excluded.prefs_json,
                   updated_at = excluded.updated_at""",
            (json.dumps(prefs, ensure_ascii=False),),
        )
        conn.commit()
        print("  Preferences: demo preferences seeded")
    except Exception:
        conn.rollback()
        raise

    # ── Recurring plan ─────────────────────────────────────────────────────────
    plan_items = [
        ("demo_leche_serenisima", 4, "must_have"),
        ("demo_huevos_lapalma",   2, "must_have"),
        ("demo_pan_fargo",        2, "must_have"),
        ("demo_yerba_taragui",    1, "recurring"),
        ("demo_arroz_gallo",      2, "recurring"),
        ("demo_fideos_matarazzo", 2, "recurring"),
        ("demo_aceite_natura",    1, "recurring"),
    ]
    priority_ids = [pid for pid, _, tag in plan_items if tag == "must_have"]

    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            """INSERT INTO recurring_plans
                   (id, household_size, monthly_budget, priority_items,
                    preferred_brands, strict_brand, excluded_categories, notes,
                    created_at, updated_at)
               VALUES ('default', 4, 20000.0, ?, ?, 0, '[]', 'Canasta mensual familiar',
                       datetime('now'), datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                   household_size      = excluded.household_size,
                   monthly_budget      = excluded.monthly_budget,
                   priority_items      = excluded.priority_items,
                   preferred_brands    = excluded.preferred_brands,
                   excluded_categories = excluded.excluded_categories,
                   notes               = excluded.notes,
                   updated_at          = excluded.updated_at""",
            (
                json.dumps(priority_ids, ensure_ascii=False),
                json.dumps({"lácteos": "La Serenísima"}, ensure_ascii=False),
            ),
        )
        # Clear and re-insert plan items
        conn.execute("DELETE FROM recurring_plan_items WHERE plan_id = 'default'")
        conn.executemany(
            "INSERT INTO recurring_plan_items (plan_id, product_id, quantity, tag) VALUES ('default', ?, ?, ?)",
            plan_items,
        )
        conn.commit()
        print(f"  Recurring plan: seeded with {len(plan_items)} items")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_profile() -> None:
    profile_path = _profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = profile_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DEMO_PROFILE, f, indent=2, ensure_ascii=False)
    tmp.replace(profile_path)

    # Invalidate the in-memory cache if the module was already loaded
    try:
        from backend.user_profile import reset_profile_cache
        reset_profile_cache()
    except Exception:
        pass

    print(f"  User profile: {profile_path.name} written (Martín García, 2 cart profiles)")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("supershop demo seed")
    print("=" * 40)

    print("\n[1/3] Catalog & semantic index")
    seed_catalog()

    print("\n[2/3] Database")
    seed_db()

    print("\n[3/3] User profile")
    seed_profile()

    print("\n" + "=" * 40)
    print("Done. Start the server with: python3 backend/server.py")
    print()
    print("Demo chat triggers:")
    print('  "quiero leche"             → resolves Leche La Serenísima')
    print('  "confirmar pedido"         → nudges about Huevos (past order, not in cart)')
    print('  "cargá mi perfil desayuno" → loads 4 breakfast items')
    print('  "generá mi canasta mensual"→ proposes monthly basket')


if __name__ == "__main__":
    main()
