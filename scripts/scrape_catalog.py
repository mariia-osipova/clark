"""
Carrefour Argentina catalog scraper.
Owner: Juan

Fetches products from the Carrefour Argentina VTEX API, normalizes them to the
supershop product schema, writes data/catalog_snapshot.json, and then calls
build_index() to generate the semantic embedding index.

Usage:
    python scripts/scrape_catalog.py [--max-products N] [--skip-index]

Environment variables:
    OPENAI_API_KEY   required for build_index() (embedding step)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
SNAPSHOT_PATH = DATA_DIR / "catalog_snapshot.json"

# ─── Carrefour VTEX API ───────────────────────────────────────────────────────

VTEX_SEARCH_URL = (
    "https://www.carrefour.com.ar/api/catalog_system/pub/products/search"
)
PAGE_SIZE = 50

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _vtex_fetch_page(from_: int, to_: int) -> list[dict]:
    url = f"{VTEX_SEARCH_URL}?_from={from_}&_to={to_}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for page {from_}-{to_}, skipping.")
        return []
    except Exception as e:
        print(f"  Error fetching page {from_}-{to_}: {e}, skipping.")
        return []


def fetch_all_products(max_products: int | None = None) -> list[dict]:
    """Paginate through VTEX search and return raw product dicts."""
    raw = []
    from_ = 0
    while True:
        to_ = from_ + PAGE_SIZE - 1
        if max_products and from_ >= max_products:
            break
        print(f"  Fetching products {from_}–{to_}…")
        page = _vtex_fetch_page(from_, to_)
        if not page:
            break
        raw.extend(page)
        if len(page) < PAGE_SIZE:
            break
        from_ += PAGE_SIZE
        time.sleep(0.4)   # be polite
    return raw


# ─── Normalization ────────────────────────────────────────────────────────────

def _extract_package_size(product: dict) -> str:
    """
    Try to extract package size from the product name or variant name.
    E.g. "Leche La Serenísima Entera 1L" → "1L"
    Falls back to the first variation value if present.
    """
    name = product.get("productName", "")
    import re
    # Match patterns like 1L, 500ml, 200g, 1kg, 6x1L, 500 ml, etc.
    m = re.search(
        r"(\d+(?:[.,]\d+)?\s*(?:kg|g|ml|l|lt|cc|un|x\d+\w*|pack))",
        name,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # Fallback: first item variation attribute value
    items = product.get("items", [])
    if items:
        for spec in items[0].get("variations", []):
            return spec  # raw string value of first variation
    return ""


def _extract_offer_label(product: dict) -> str:
    """
    Extract a human-readable offer label from VTEX promotion teasers,
    or derive one from the discount percentage.
    """
    items = product.get("items", [])
    if not items:
        return ""
    offer = items[0].get("sellers", [{}])[0].get("commertialOffer", {})

    # VTEX promotions
    teasers = offer.get("Teasers", [])
    if teasers:
        # Teasers can be dicts with a <Name> field or plain strings
        first = teasers[0]
        if isinstance(first, dict):
            return first.get("Name", "") or first.get("<Name>", "")
        return str(first)

    # Derive from discount
    list_price = offer.get("ListPrice", 0)
    price = offer.get("Price", 0)
    if list_price and price and list_price > price:
        pct = round((1 - price / list_price) * 100)
        if pct >= 5:
            return f"{pct}% OFF"
    return ""


def normalize_product(raw: dict) -> dict | None:
    """
    Map a raw VTEX product dict to the supershop normalized schema.
    Returns None if the product has no valid price.
    """
    items = raw.get("items", [])
    if not items:
        return None
    item = items[0]
    sellers = item.get("sellers", [])
    if not sellers:
        return None
    offer = sellers[0].get("commertialOffer", {})

    price = float(offer.get("Price", 0) or 0)
    list_price = float(offer.get("ListPrice", 0) or 0)
    available_qty = int(offer.get("AvailableQuantity", 0) or 0)

    if price <= 0:
        return None

    if list_price <= 0:
        list_price = price

    discount_pct = 0.0
    if list_price > price:
        discount_pct = round((1 - price / list_price) * 100, 1)

    images = item.get("images", [])
    image_url = images[0].get("imageUrl", "") if images else ""

    # Category: use the last segment of the first category path
    categories = raw.get("categories", [])
    category = ""
    if categories:
        # categories are like ["/Almacén/Aceites/", "/Almacén/"]
        # take the deepest (longest) one and strip to last segment
        deepest = max(categories, key=len)
        parts = [p for p in deepest.strip("/").split("/") if p]
        category = parts[-1] if parts else ""

    return {
        "id": str(raw.get("productId", "")),
        "name": raw.get("productName", ""),
        "brand": raw.get("brand", ""),
        "package_size": _extract_package_size(raw),
        "price": price,
        "list_price": list_price,
        "discount_pct": discount_pct,
        "offer_label": _extract_offer_label(raw),
        "image_url": image_url,
        "available_quantity": available_qty,
        "category": category,
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape Carrefour Argentina catalog.")
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="Stop after this many raw products (for quick test runs)",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Skip the embedding index build step",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("=== Fetching Carrefour catalog ===")
    raw_products = fetch_all_products(max_products=args.max_products)
    print(f"Fetched {len(raw_products)} raw products.")

    print("=== Normalizing ===")
    catalog = []
    skipped = 0
    for raw in raw_products:
        normalized = normalize_product(raw)
        if normalized:
            catalog.append(normalized)
        else:
            skipped += 1

    print(f"Normalized: {len(catalog)} products ({skipped} skipped — no price).")

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    print(f"Snapshot written to {SNAPSHOT_PATH}")

    if not args.skip_index:
        print("=== Building semantic index (embeddings) ===")
        from backend.product_semantic_index import build_index
        build_index(catalog)

    print("=== Done ===")
    print(f"  Products in snapshot : {len(catalog)}")
    discounted = [p for p in catalog if p["discount_pct"] > 0]
    print(f"  Products with offers : {len(discounted)}")
    unavailable = [p for p in catalog if p["available_quantity"] == 0]
    print(f"  Unavailable items    : {len(unavailable)}")


if __name__ == "__main__":
    main()
