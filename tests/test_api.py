"""
Tests for Nacho's V1 backend tasks:
  - envelope() helper
  - _validate_order_cart() — cart validation and price enforcement
  - Order total computation
  - GET/POST /api/v1/orders (integration)
  - GET /api/v1/catalog (integration)

No OpenAI API key required.
"""

import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import _validate_order_cart, envelope


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_catalog():
    return [
        {
            "id": "p1",
            "name": "Leche entera La Serenísima",
            "brand": "La Serenísima",
            "package_size": "1L",
            "price": 350.0,
            "available_quantity": 10,
            "image_url": "https://example.com/leche.jpg",
        },
        {
            "id": "p2",
            "name": "Yogur frutilla Danone",
            "brand": "Danone",
            "package_size": "200g",
            "price": 180.0,
            "available_quantity": 5,
            "image_url": "https://example.com/yogur.jpg",
        },
        {
            "id": "p3",
            "name": "Pan lactal Bimbo",
            "brand": "Bimbo",
            "package_size": "500g",
            "price": 420.0,
            "available_quantity": 0,  # out of stock
            "image_url": "",
        },
    ]


@pytest.fixture
def test_server(tmp_path):
    """Spin up a real ThreadingHTTPServer on a random port for integration tests."""
    db_path = tmp_path / "test.db"
    os.environ["DB_PATH"] = str(db_path)
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")

    from backend.db import init_db
    from backend.app import RequestHandler

    init_db()

    server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
    del os.environ["DB_PATH"]


def _get(url: str) -> dict:
    with urlopen(url) as r:
        return json.loads(r.read())


def _post(url: str, body: dict) -> tuple[dict, int]:
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req) as r:
            return json.loads(r.read()), r.status
    except HTTPError as e:
        return json.loads(e.read()), e.code


# ─── Envelope tests ───────────────────────────────────────────────────────────

class TestEnvelope:
    def test_ok_true_when_no_error(self):
        assert envelope(data={"x": 1})["ok"] is True

    def test_ok_false_when_error_given(self):
        result = envelope(error="oops")
        assert result["ok"] is False
        assert result["error"] == "oops"

    def test_always_has_request_id(self):
        result = envelope()
        assert "request_id" in result
        assert len(result["request_id"]) > 0

    def test_custom_request_id_preserved(self):
        assert envelope(request_id="my-id")["request_id"] == "my-id"

    def test_data_defaults_to_empty_dict(self):
        assert envelope()["data"] == {}

    def test_error_none_when_not_given(self):
        assert envelope(data={})["error"] is None


# ─── Cart validation tests ────────────────────────────────────────────────────

class TestValidateOrderCart:
    def test_valid_item_passes_through(self, sample_catalog):
        result = _validate_order_cart([{"product_id": "p1", "quantity": 2}], sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"
        assert result[0]["quantity"] == 2

    def test_price_comes_from_catalog_not_client(self, sample_catalog):
        result = _validate_order_cart([{"product_id": "p1", "quantity": 1, "price": 9999.0}], sample_catalog)
        assert result[0]["price"] == 350.0

    def test_unknown_product_is_dropped(self, sample_catalog):
        assert _validate_order_cart([{"product_id": "nonexistent", "quantity": 1}], sample_catalog) == []

    def test_out_of_stock_is_dropped(self, sample_catalog):
        assert _validate_order_cart([{"product_id": "p3", "quantity": 1}], sample_catalog) == []

    def test_quantity_floored_to_one(self, sample_catalog):
        result = _validate_order_cart([{"product_id": "p1", "quantity": 0}], sample_catalog)
        assert result[0]["quantity"] == 1

    def test_catalog_fields_populated(self, sample_catalog):
        result = _validate_order_cart([{"product_id": "p2", "quantity": 1}], sample_catalog)
        item = result[0]
        assert item["name"] == "Yogur frutilla Danone"
        assert item["brand"] == "Danone"
        assert item["package_size"] == "200g"
        assert item["image_url"] == "https://example.com/yogur.jpg"

    def test_empty_cart_returns_empty(self, sample_catalog):
        assert _validate_order_cart([], sample_catalog) == []

    def test_empty_catalog_drops_all(self):
        assert _validate_order_cart([{"product_id": "p1", "quantity": 1}], []) == []

    def test_mixed_valid_and_invalid(self, sample_catalog):
        cart = [
            {"product_id": "p1", "quantity": 1},
            {"product_id": "bad_id", "quantity": 1},
            {"product_id": "p3", "quantity": 1},  # out of stock
        ]
        result = _validate_order_cart(cart, sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"


# ─── Total computation tests ──────────────────────────────────────────────────

class TestOrderTotal:
    def test_total_uses_catalog_price(self, sample_catalog):
        validated = _validate_order_cart([{"product_id": "p1", "quantity": 2}], sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 700.0

    def test_total_ignores_client_price(self, sample_catalog):
        validated = _validate_order_cart([{"product_id": "p1", "quantity": 1, "price": 1.0}], sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 350.0

    def test_empty_cart_total_is_zero(self, sample_catalog):
        validated = _validate_order_cart([], sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 0.0

    def test_multi_item_total(self, sample_catalog):
        cart = [
            {"product_id": "p1", "quantity": 1},  # 350
            {"product_id": "p2", "quantity": 2},  # 360
        ]
        validated = _validate_order_cart(cart, sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 710.0


# ─── Integration: catalog endpoint ───────────────────────────────────────────

class TestCatalogEndpoint:
    def test_returns_envelope_structure(self, test_server):
        result = _get(f"{test_server}/api/v1/catalog")
        assert "ok" in result
        assert "data" in result
        assert "error" in result
        assert "request_id" in result

    def test_ok_is_true(self, test_server):
        result = _get(f"{test_server}/api/v1/catalog")
        assert result["ok"] is True

    def test_data_has_products_list(self, test_server):
        result = _get(f"{test_server}/api/v1/catalog")
        assert isinstance(result["data"]["products"], list)

    def test_data_has_total(self, test_server):
        result = _get(f"{test_server}/api/v1/catalog")
        assert "total" in result["data"]


# ─── Integration: orders endpoint ────────────────────────────────────────────

class TestOrdersEndpoint:
    def test_post_returns_order_id(self, test_server):
        result, status = _post(f"{test_server}/api/v1/orders", {"cart": []})
        assert result["ok"] is True
        assert "order_id" in result["data"]

    def test_post_empty_cart_total_is_zero(self, test_server):
        result, _ = _post(f"{test_server}/api/v1/orders", {"cart": []})
        assert result["data"]["total"] == 0.0

    def test_get_returns_orders_list(self, test_server):
        result = _get(f"{test_server}/api/v1/orders")
        assert result["ok"] is True
        assert isinstance(result["data"]["orders"], list)

    def test_posted_order_appears_in_get(self, test_server):
        _post(f"{test_server}/api/v1/orders", {"cart": []})
        result = _get(f"{test_server}/api/v1/orders")
        assert len(result["data"]["orders"]) >= 1

    def test_order_has_required_fields(self, test_server):
        _post(f"{test_server}/api/v1/orders", {"cart": []})
        orders = _get(f"{test_server}/api/v1/orders")["data"]["orders"]
        order = orders[0]
        assert "id" in order
        assert "total" in order
        assert "created_at" in order
        assert "items" in order

    def test_multiple_orders_all_returned(self, test_server):
        _post(f"{test_server}/api/v1/orders", {"cart": []})
        _post(f"{test_server}/api/v1/orders", {"cart": []})
        result = _get(f"{test_server}/api/v1/orders")
        assert len(result["data"]["orders"]) >= 2

    def test_unknown_endpoint_returns_404(self, test_server):
        result, status = _post(f"{test_server}/api/v1/nonexistent", {})
        assert status == 404
        assert result["ok"] is False
