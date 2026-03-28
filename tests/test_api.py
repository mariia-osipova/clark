"""
Tests for Nacho's backend tasks:
  - envelope() helper
  - _validate_order_cart() — cart validation and price enforcement
  - Order total computation
  - GET/POST /api/v1/orders (integration)
  - GET /api/v1/catalog (integration)
  - GET/PUT /api/v1/preferences (integration)
  - _assemble_chat_context() — order history + preferences context
  - _validate_clarification_response() — V3 clarification contract
  - _validate_chat_body() — V3 defensive chat body validation

No OpenAI API key required.
"""

import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import (
    _assemble_chat_context,
    _validate_chat_body,
    _validate_clarification_response,
    _validate_order_cart,
    envelope,
)


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


# ─── Integration: preferences endpoint ───────────────────────────────────────

class TestPreferencesEndpoint:
    def test_get_returns_empty_prefs_by_default(self, test_server):
        result = _get(f"{test_server}/api/v1/preferences")
        assert result["ok"] is True
        assert result["data"]["preferences"] == {}

    def test_put_saves_preferences(self, test_server):
        prefs = {"notes": "sin gluten", "excluded_categories": ["bebidas alcohólicas"]}
        result, status = _post(f"{test_server}/api/v1/preferences", {"preferences": prefs})
        assert result["ok"] is True
        assert result["data"]["preferences"] == prefs

    def test_get_returns_saved_preferences(self, test_server):
        prefs = {"notes": "vegano", "preferred_brands": {"leche": "Almond"}}
        _post(f"{test_server}/api/v1/preferences", {"preferences": prefs})
        result = _get(f"{test_server}/api/v1/preferences")
        assert result["data"]["preferences"] == prefs

    def test_put_overwrites_existing_preferences(self, test_server):
        _post(f"{test_server}/api/v1/preferences", {"preferences": {"notes": "viejo"}})
        _post(f"{test_server}/api/v1/preferences", {"preferences": {"notes": "nuevo"}})
        result = _get(f"{test_server}/api/v1/preferences")
        assert result["data"]["preferences"]["notes"] == "nuevo"

    def test_get_returns_updated_at(self, test_server):
        _post(f"{test_server}/api/v1/preferences", {"preferences": {}})
        result = _get(f"{test_server}/api/v1/preferences")
        assert result["data"]["updated_at"] is not None

    def test_put_invalid_body_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/preferences", {"preferences": "not-a-dict"})
        assert status == 400
        assert result["ok"] is False


# ─── Unit: chat context assembly ─────────────────────────────────────────────

class TestAssembleChatContext:
    def test_returns_none_when_no_data(self, tmp_path):
        os.environ["DB_PATH"] = str(tmp_path / "test.db")
        from backend.db import init_db
        init_db()
        result = _assemble_chat_context()
        assert result is None

    def test_includes_order_history_when_orders_exist(self, tmp_path):
        os.environ["DB_PATH"] = str(tmp_path / "ctx.db")
        from backend.db import init_db, get_db
        init_db()
        conn = get_db()
        conn.execute(
            "INSERT INTO orders (id, cart_json, total) VALUES (?, ?, ?)",
            ("ord1", '[{"name": "Leche", "quantity": 2}]', 700.0),
        )
        conn.commit()
        conn.close()
        result = _assemble_chat_context()
        assert result is not None
        assert "Leche" in result
        assert "700.00" in result

    def test_includes_preferences_when_saved(self, tmp_path):
        os.environ["DB_PATH"] = str(tmp_path / "pref.db")
        from backend.db import init_db, get_db
        init_db()
        conn = get_db()
        conn.execute(
            "INSERT INTO preferences (key, prefs_json) VALUES ('default', ?)",
            ('{"notes": "sin tacc", "excluded_categories": ["lácteos"]}',),
        )
        conn.commit()
        conn.close()
        result = _assemble_chat_context()
        assert result is not None
        assert "sin tacc" in result
        assert "lácteos" in result

    def test_returns_none_on_missing_db(self, tmp_path):
        os.environ["DB_PATH"] = str(tmp_path / "nonexistent.db")
        result = _assemble_chat_context()
        assert result is None


class TestServeStaticSecurity:
    """Path traversal protection for _serve_static."""

    def _make_handler(self):
        from backend.app import RequestHandler

        handler = RequestHandler.__new__(RequestHandler)
        handler.server = MagicMock()
        handler.client_address = ("127.0.0.1", 9999)
        handler.requestline = "GET / HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "GET"
        handler.headers = MagicMock()
        handler._headers_buf = []
        handler.wfile = MagicMock()
        handler.rfile = MagicMock()
        return handler

    def test_path_traversal_returns_403(self):
        handler = self._make_handler()
        responses = []
        handler.send_response = lambda code: responses.append(code)
        handler.end_headers = MagicMock()
        handler.send_header = MagicMock()

        handler._serve_static("/../backend/chat_agent_agentic.py")

        assert 403 in responses

    def test_normal_path_is_not_blocked(self, tmp_path):
        import backend.app as app_module

        fake_frontend = tmp_path / "frontend"
        fake_frontend.mkdir()
        (fake_frontend / "index.html").write_bytes(b"<html></html>")

        handler = self._make_handler()
        responses = []
        handler.send_response = lambda code: responses.append(code)
        handler.end_headers = MagicMock()
        handler.send_header = MagicMock()
        handler.wfile = MagicMock()

        from unittest.mock import patch

        with patch.object(app_module, "ROOT", tmp_path):
            handler._serve_static("/index.html")

        assert 200 in responses


# ─── V3: clarification response validation ───────────────────────────────────

class TestValidateClarificationResponse:
    def test_none_returns_none(self):
        assert _validate_clarification_response(None) is None

    def test_valid_payload_passes_through(self):
        raw = {"pending_request_id": "abc-123", "chosen_option_id": "opt1"}
        result = _validate_clarification_response(raw)
        assert result == {"pending_request_id": "abc-123", "chosen_option_id": "opt1"}

    def test_non_dict_returns_none(self):
        assert _validate_clarification_response("string") is None
        assert _validate_clarification_response(42) is None
        assert _validate_clarification_response([]) is None

    def test_missing_pending_request_id_returns_none(self):
        assert _validate_clarification_response({"chosen_option_id": "opt1"}) is None

    def test_missing_chosen_option_id_returns_none(self):
        assert _validate_clarification_response({"pending_request_id": "abc-123"}) is None

    def test_empty_pending_request_id_returns_none(self):
        assert _validate_clarification_response({"pending_request_id": "  ", "chosen_option_id": "opt1"}) is None

    def test_empty_chosen_option_id_returns_none(self):
        assert _validate_clarification_response({"pending_request_id": "abc", "chosen_option_id": ""}) is None

    def test_non_string_fields_return_none(self):
        assert _validate_clarification_response({"pending_request_id": 123, "chosen_option_id": "opt1"}) is None

    def test_strips_whitespace(self):
        raw = {"pending_request_id": " abc-123 ", "chosen_option_id": " opt1 "}
        result = _validate_clarification_response(raw)
        assert result["pending_request_id"] == "abc-123"
        assert result["chosen_option_id"] == "opt1"


# ─── V3: chat body validation ─────────────────────────────────────────────────

class TestValidateChatBody:
    def test_valid_body_passes_through(self):
        body = {"message": "hola", "history": [], "cart": []}
        msg, hist, cart, clarification, err = _validate_chat_body(body)
        assert msg == "hola"
        assert hist == []
        assert cart == []
        assert clarification is None
        assert err is None

    def test_non_string_message_returns_error(self):
        _, _, _, _, err = _validate_chat_body({"message": 123})
        assert err is not None

    def test_message_is_stripped(self):
        msg, _, _, _, err = _validate_chat_body({"message": "  hola  "})
        assert msg == "hola"
        assert err is None

    def test_non_list_history_is_discarded(self):
        _, hist, _, _, err = _validate_chat_body({"message": "hi", "history": "bad"})
        assert hist == []
        assert err is None

    def test_invalid_history_items_are_filtered(self):
        history = [
            {"role": "user", "content": "valid"},
            {"role": "user"},          # missing content
            {"content": "no role"},    # missing role
            "not a dict",
        ]
        _, hist, _, _, _ = _validate_chat_body({"message": "hi", "history": history})
        assert len(hist) == 1
        assert hist[0]["content"] == "valid"

    def test_non_list_cart_is_discarded(self):
        _, _, cart, _, err = _validate_chat_body({"message": "hi", "cart": "bad"})
        assert cart == []
        assert err is None

    def test_cart_items_without_product_id_are_filtered(self):
        cart = [
            {"product_id": "p1", "quantity": 1},
            {"quantity": 2},  # no product_id
        ]
        _, _, result_cart, _, _ = _validate_chat_body({"message": "hi", "cart": cart})
        assert len(result_cart) == 1
        assert result_cart[0]["product_id"] == "p1"

    def test_clarification_response_is_validated(self):
        body = {
            "message": "hi",
            "clarification_response": {"pending_request_id": "x", "chosen_option_id": "y"},
        }
        _, _, _, clarification, _ = _validate_chat_body(body)
        assert clarification == {"pending_request_id": "x", "chosen_option_id": "y"}

    def test_malformed_clarification_response_is_ignored(self):
        body = {"message": "hi", "clarification_response": "bad"}
        _, _, _, clarification, _ = _validate_chat_body(body)
        assert clarification is None

    def test_empty_body_returns_defaults(self):
        msg, hist, cart, clarification, err = _validate_chat_body({})
        assert msg == ""
        assert hist == []
        assert cart == []
        assert clarification is None
        assert err is None


# ─── V3: integration — defensive validation on endpoints ─────────────────────

class TestDefensiveValidation:
    def test_orders_post_non_list_cart_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/orders", {"cart": "bad"})
        assert status == 400
        assert result["ok"] is False

    def test_chat_post_non_string_message_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/chat", {"message": 999})
        assert status == 400
        assert result["ok"] is False

    def test_chat_post_malformed_clarification_returns_400(self, test_server):
        # Non-string message + malformed clarification — caught by body validation before OpenAI
        result, status = _post(f"{test_server}/api/v1/chat", {"message": 999, "clarification_response": "garbage"})
        assert status == 400
        assert result["ok"] is False
