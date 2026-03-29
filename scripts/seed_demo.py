#!/usr/bin/env python3
"""
Seed the demo database for the Jaime demo persona.

Idempotent — safe to re-run. Clears demo-seeded rows before inserting
and rewrites the demo profile to a known-good minimal shape.

Usage:
    python3 scripts/seed_demo.py
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "proxy_store.db"))
PROFILE_PATH = Path(os.environ.get("USER_PROFILE_PATH", ROOT / "data" / "user_profile.json"))

# ── Product catalog snapshots for order cart_json ──────────────────────────────
# Pulled from data/catalog_snapshot.json on 2026-03-28.

MILK = {
    "product_id": "720719",
    "name": "Leche La serenisima clásica 3% 1L.",
    "brand": "La Serenísima",
    "package_size": "1L",
    "price": 2570.0,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/636141/7790742363008_01.jpg.jpg?v=638780812788100000",
}
EGGS = {
    "product_id": "380313",
    "name": "Huevos blancos El Mercado 6 uni",
    "brand": "El Mercado",
    "package_size": "6 un",
    "price": 1922.8,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/834041/7798108344487_02.jpg?v=639102195261730000",
}
BREAD = {
    "product_id": "758014",
    "name": "Pan con Cereales y Semillas Fargo 400 grs",
    "brand": "Fargo",
    "package_size": "400 g",
    "price": 5395.0,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/687486/7793890261493_01.jpg?v=638900815850570000",
}
BUTTER = {
    "product_id": "495133",
    "name": "Manteca Classic calidad extra 200 g.",
    "brand": "Carrefour",
    "package_size": "200 g",
    "price": 2928.1,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/386972/7798152226326_E01.jpg?v=638852742884200000",
}
YERBA = {
    "product_id": "669429",
    "name": "Yerba mate Playadito suave con palo 1 kg.",
    "brand": "Playadito",
    "package_size": "1 kg",
    "price": 4790.0,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/207097/7793704000928_03.jpg?v=637623985987930000",
}
RICE = {
    "product_id": "718787",
    "name": "Arroz parboil Gallo oro en bolsa 1 kg.",
    "brand": "Gallo",
    "package_size": "1 kg",
    "price": 2500.0,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/318039/7790070431417_02.jpg?v=638180311212270000",
}
PASTA = {
    "product_id": "726311",
    "name": "Fideos tallarin N5 Lucchetti 500 g.",
    "brand": "Matarazzo",
    "package_size": "500 g",
    "price": 1499.0,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/833465/7790070336118_02.jpg?v=639101356962770000",
}
OIL = {
    "product_id": "699030",
    "name": "Aceite de girasol Carrefour Classic alto omega pet 900 cc.",
    "brand": "Carrefour",
    "package_size": "900 cc",
    "price": 2998.6,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/603859/7791720025291_01.jpg?v=638700617422870000",
}
TOILET_PAPER = {
    "product_id": "736162",
    "name": "Papel higienico doble hoja Higienol plus x4 30 mts.",
    "brand": "Higienol",
    "package_size": "",
    "price": 4045.0,
    "image_url": "https://carrefourar.vteximg.com.br/arquivos/ids/664417/7790250015536_01.jpg?v=638835174998930000",
}


def _cart_item(product: dict, quantity: int) -> dict:
    return {**product, "quantity": quantity}


def _total(items: list[dict]) -> float:
    return sum(i["price"] * i["quantity"] for i in items)


def _build_profile(last_order_date: str) -> dict:
    return {
        "_schema_version": 1,
        "_doc": "Demo user — Jaime. Hardcoded for hackathon demo. Product IDs reference data/catalog_snapshot.json.",
        "identity": {
            "name": "Jaime",
            "locale": "es-AR",
        },
        "household": {
            "size": 1,
            "has_children": False,
            "has_pets": False,
            "dietary_restrictions": [],
        },
        "preferences": {
            "preferred_brands": {
                "lácteos": "La Serenísima",
            },
            "excluded_brands": [],
            "excluded_categories": [],
            "budget_monthly": 15000,
            "strict_brand": False,
        },
        "purchase_history": {
            "frequent_items": [
                MILK["product_id"],
                BREAD["product_id"],
                BUTTER["product_id"],
                TOILET_PAPER["product_id"],
            ],
            "last_order_date": last_order_date,
            "avg_cart_size": 3,
            "shopping_frequency_days": 14,
        },
        "interaction": {
            "auto_pick_suggestions": False,
            "verbosity": "normal",
        },
        "cart_profiles": {
            "desayuno": [
                {"product_id": MILK["product_id"], "quantity": 1},
                {"product_id": BREAD["product_id"], "quantity": 1},
                {"product_id": BUTTER["product_id"], "quantity": 1},
            ],
            "despensa": [
                {"product_id": RICE["product_id"], "quantity": 1},
                {"product_id": PASTA["product_id"], "quantity": 1},
                {"product_id": OIL["product_id"], "quantity": 1},
                {"product_id": YERBA["product_id"], "quantity": 1},
            ],
        },
    }


def _write_profile(last_order_date: str) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(_build_profile(last_order_date), f, indent=2, ensure_ascii=False)
        f.write("\n")


def seed(db: sqlite3.Connection) -> None:
    now = datetime.utcnow()

    # ── Orders ────────────────────────────────────────────────────────────────
    # Delete any previously seeded demo orders so this is idempotent.
    db.execute("DELETE FROM orders WHERE id LIKE 'demo-%'")

    # Last order matches frequent_items so the forgotten-item nudge fires
    # for items Jaime usually buys but hasn't added to the current cart.
    order_items = [
        _cart_item(MILK, 1),
        _cart_item(BREAD, 1),
        _cart_item(BUTTER, 1),
        _cart_item(TOILET_PAPER, 1),
    ]
    order_date = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO orders (id, cart_json, total, created_at) VALUES (?, ?, ?, ?)",
        (
            f"demo-{uuid.uuid4().hex[:8]}",
            json.dumps(order_items, ensure_ascii=False),
            _total(order_items),
            order_date,
        ),
    )

    # ── Preferences ───────────────────────────────────────────────────────────
    prefs = {
        "notes": "Compras simples para Jaime. Si hay lácteos, priorizá La Serenísima.",
        "preferred_brands": {"lácteos": "La Serenísima"},
        "household_size": 1,
        "excluded_categories": [],
    }
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, prefs_json, updated_at) VALUES (?, ?, ?)",
        ("default", json.dumps(prefs, ensure_ascii=False), now.strftime("%Y-%m-%d %H:%M:%S")),
    )

    # ── Recurring plan ────────────────────────────────────────────────────────
    db.execute("DELETE FROM recurring_plan_items WHERE plan_id = 'default'")
    db.execute("DELETE FROM recurring_plans WHERE id = 'default'")

    plan_id = "default"
    db.execute(
        """INSERT INTO recurring_plans
               (id, household_size, monthly_budget, priority_items, preferred_brands,
                strict_brand, excluded_categories, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan_id,
            1,
            15000.0,
            json.dumps(["leche La Serenísima", "huevos", "pan Fargo", "yerba mate"]),
            json.dumps({"lácteos": "La Serenísima"}),
            0,
            json.dumps([]),
            "Canasta mensual simple de Jaime",
            now.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    must_have = [
        (MILK["product_id"], 1),
        (EGGS["product_id"], 1),
    ]
    recurring = [
        (YERBA["product_id"], 1),
        (BREAD["product_id"], 1),
    ]
    for pid, qty in must_have:
        db.execute(
            "INSERT INTO recurring_plan_items (plan_id, product_id, quantity, tag) VALUES (?, ?, ?, ?)",
            (plan_id, pid, qty, "must_have"),
        )
    for pid, qty in recurring:
        db.execute(
            "INSERT INTO recurring_plan_items (plan_id, product_id, quantity, tag) VALUES (?, ?, ?, ?)",
            (plan_id, pid, qty, "recurring"),
        )

    db.commit()
    _write_profile(order_date)


def main() -> None:
    print(f"Seeding demo data into {DB_PATH} ...")
    with sqlite3.connect(DB_PATH) as db:
        seed(db)
    print("Done.")
    print(f"  ✓ demo profile written to {PROFILE_PATH}")
    print("  ✓ 1 demo order from two weeks ago")
    print("  ✓ preferences row (key='default')")
    print("  ✓ recurring plan 'default' (4 simple recurring items)")


if __name__ == "__main__":
    main()
