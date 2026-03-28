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
from unittest.mock import MagicMock, patch
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
    pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
    pass
    del os.environ["DB_PATH"]


def _get(url: str) -> dict:
    with urlopen(url) as r:
        return json.loads(r.read())


def _post(url: str, body: dict, headers: dict | None = None) -> tuple[dict, int]:
    data = json.dumps(body).encode()
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    req = Request(url, data=data, headers=merged_headers, method="POST")
    try:
        with urlopen(req) as r:
            return json.loads(r.read()), r.status
    except HTTPError as e:
        return json.loads(e.read()), e.code


def _graph_state(reply="ok", result_cart=None, clarification=None):
    return {
        "reply": reply,
        "resolved_cart": result_cart,
        "pending_clarification": clarification,
        "missing_items": [],
    }


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
        result, _ = _validate_order_cart([{"product_id": "p1", "quantity": 2}], sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"
        assert result[0]["quantity"] == 2

    def test_price_comes_from_catalog_not_client(self, sample_catalog):
        result, _ = _validate_order_cart([{"product_id": "p1", "quantity": 1, "price": 9999.0}], sample_catalog)
        assert result[0]["price"] == 350.0

    def test_unknown_product_is_dropped(self, sample_catalog):
        result, _ = _validate_order_cart([{"product_id": "nonexistent", "quantity": 1}], sample_catalog)
        assert result == []

    def test_out_of_stock_is_dropped(self, sample_catalog):
        result, dropped = _validate_order_cart([{"product_id": "p3", "quantity": 1}], sample_catalog)
        assert result == []
        assert dropped == ["Pan lactal Bimbo"]

    def test_quantity_floored_to_one(self, sample_catalog):
        result, _ = _validate_order_cart([{"product_id": "p1", "quantity": 0}], sample_catalog)
        assert result[0]["quantity"] == 1

    def test_catalog_fields_populated(self, sample_catalog):
        result, _ = _validate_order_cart([{"product_id": "p2", "quantity": 1}], sample_catalog)
        item = result[0]
        assert item["name"] == "Yogur frutilla Danone"
        assert item["brand"] == "Danone"
        assert item["package_size"] == "200g"
        assert item["image_url"] == "https://example.com/yogur.jpg"

    def test_empty_cart_returns_empty(self, sample_catalog):
        result, dropped = _validate_order_cart([], sample_catalog)
        assert result == []
        assert dropped == []

    def test_empty_catalog_drops_all(self):
        result, _ = _validate_order_cart([{"product_id": "p1", "quantity": 1}], [])
        assert result == []

    def test_mixed_valid_and_invalid(self, sample_catalog):
        cart = [
            {"product_id": "p1", "quantity": 1},
            {"product_id": "bad_id", "quantity": 1},
            {"product_id": "p3", "quantity": 1},  # out of stock
        ]
        result, dropped = _validate_order_cart(cart, sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"
        assert "Pan lactal Bimbo" in dropped

    def test_dropped_names_returned(self, sample_catalog):
        cart = [{"product_id": "p3", "quantity": 1}, {"product_id": "p2", "quantity": 1}]
        result, dropped = _validate_order_cart(cart, sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p2"
        assert dropped == ["Pan lactal Bimbo"]


# ─── Total computation tests ──────────────────────────────────────────────────

class TestOrderTotal:
    def test_total_uses_catalog_price(self, sample_catalog):
        validated, _ = _validate_order_cart([{"product_id": "p1", "quantity": 2}], sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 700.0

    def test_total_ignores_client_price(self, sample_catalog):
        validated, _ = _validate_order_cart([{"product_id": "p1", "quantity": 1, "price": 1.0}], sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 350.0

    def test_empty_cart_total_is_zero(self, sample_catalog):
        validated, _ = _validate_order_cart([], sample_catalog)
        total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
        assert total == 0.0

    def test_multi_item_total(self, sample_catalog):
        cart = [
            {"product_id": "p1", "quantity": 1},  # 350
            {"product_id": "p2", "quantity": 2},  # 360
        ]
        validated, _ = _validate_order_cart(cart, sample_catalog)
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
    """V4: checkout reads from server session cart, not request body."""

    def _place_order(self, base_url: str, sess: str, sample_catalog):
        """Helper: add p1 to server cart and checkout (with mocked catalog)."""
        import backend.app as app_module
        _post(f"{base_url}/api/v1/cart", {"session_id": sess, "product_id": "p1", "quantity": 1})
        with patch.object(app_module, "_load_catalog", return_value=sample_catalog):
            return _post(f"{base_url}/api/v1/orders", {}, headers={"X-Session-Token": sess})

    def test_post_requires_session_id(self, test_server):
        result, status = _post(f"{test_server}/api/v1/orders", {})
        assert status == 400
        assert result["ok"] is False

    def test_post_empty_server_cart_returns_400(self, test_server, sample_catalog):
        import backend.app as app_module
        with patch.object(app_module, "_load_catalog", return_value=sample_catalog):
            result, status = _post(f"{test_server}/api/v1/orders", {},
                                   headers={"X-Session-Token": "orders-empty-sess"})
        assert status == 400
        assert result["ok"] is False

    def test_post_returns_order_id(self, test_server, sample_catalog):
        result, status = self._place_order(test_server, "orders-sess-1", sample_catalog)
        assert status == 200
        assert result["ok"] is True
        assert "order_id" in result["data"]

    def test_post_total_computed_from_catalog(self, test_server, sample_catalog):
        import backend.app as app_module
        sess = "orders-total-sess"
        _post(f"{test_server}/api/v1/cart", {"session_id": sess, "product_id": "p1", "quantity": 2})
        with patch.object(app_module, "_load_catalog", return_value=sample_catalog):
            result, status = _post(f"{test_server}/api/v1/orders", {},
                                   headers={"X-Session-Token": sess})
        assert status == 200
        assert result["data"]["total"] == 700.0  # 350 * 2

    def test_get_returns_orders_list(self, test_server):
        result = _get(f"{test_server}/api/v1/orders")
        assert result["ok"] is True
        assert isinstance(result["data"]["orders"], list)

    def test_posted_order_appears_in_get(self, test_server, sample_catalog):
        self._place_order(test_server, "orders-appear-sess", sample_catalog)
        result = _get(f"{test_server}/api/v1/orders")
        assert len(result["data"]["orders"]) >= 1

    def test_order_has_required_fields(self, test_server, sample_catalog):
        self._place_order(test_server, "orders-fields-sess", sample_catalog)
        orders = _get(f"{test_server}/api/v1/orders")["data"]["orders"]
        order = orders[0]
        assert "id" in order
        assert "total" in order
        assert "created_at" in order
        assert "items" in order

    def test_multiple_orders_all_returned(self, test_server, sample_catalog):
        self._place_order(test_server, "orders-multi-1", sample_catalog)
        self._place_order(test_server, "orders-multi-2", sample_catalog)
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


class TestChatEndpoint:
    def test_clarification_round_trip(self, test_server, sample_catalog):
        clarification = {
            "question": "¿Cuál leche querés?",
            "options": [
                {"id": "p1", "label": "Leche entera La Serenísima 1L", "product": sample_catalog[0]},
                {"id": "p2", "label": "Yogur frutilla Danone", "product": sample_catalog[1]},
            ],
            "pending_request_id": "pending-1",
        }
        mock_app = MagicMock()
        mock_app.invoke.side_effect = [
            _graph_state(reply="¿Cuál leche querés?", clarification=clarification),
            _graph_state(
                reply="Perfecto, agregué la leche elegida.",
                result_cart=[{
                    "product_id": "p1",
                    "name": "Leche entera La Serenísima",
                    "brand": "La Serenísima",
                    "package_size": "1L",
                    "price": 350.0,
                    "quantity": 1,
                    "image_url": "https://example.com/leche.jpg",
                }],
            ),
        ]

        with patch("backend.chat_agent_agentic._load_catalog", return_value=sample_catalog), patch(
            "backend.chat_agent_agentic._build_graph", return_value=mock_app
        ):
            first, status1 = _post(
                f"{test_server}/api/v1/chat",
                {"message": "quiero leche", "history": [], "cart": []},
            )
            second, status2 = _post(
                f"{test_server}/api/v1/chat",
                {
                    "message": "Leche entera La Serenísima 1L",
                    "history": [{"role": "assistant", "content": first["data"]["reply"]}],
                    "cart": [],
                    "clarification_response": {
                        "pending_request_id": "pending-1",
                        "chosen_option_id": "p1",
                    },
                },
            )

        assert status1 == 200
        assert status2 == 200
        assert first["data"]["clarification"]["pending_request_id"] == "pending-1"
        assert second["data"]["cart"][0]["product_id"] == "p1"

    def test_typed_clarification_reply_uses_session_header(self, test_server, sample_catalog):
        from backend.chat_agent_agentic import _reset_app_cache

        _reset_app_cache()
        clarification = {
            "question": "¿Cuál leche querés?",
            "options": [
                {"id": "p1", "label": "Leche entera La Serenísima 1L", "product": sample_catalog[0]},
                {"id": "p2", "label": "Yogur frutilla Danone 200g", "product": sample_catalog[1]},
            ],
            "pending_request_id": "pending-1",
        }
        mock_app = MagicMock()
        # Turn 1: new session (empty state), returns clarification
        # Turn 2: continuation after user picks an option, returns no clarification
        empty_state = MagicMock()
        empty_state.values = {}
        mock_app.get_state.return_value = empty_state
        mock_app.invoke.side_effect = [
            _graph_state(reply="¿Cuál leche querés?", clarification=clarification),
            _graph_state(reply="Listo, agregué Leche entera La Serenísima 1L al carrito."),
        ]

        import backend.app as app_module
        with patch("backend.chat_agent_agentic._load_catalog", return_value=sample_catalog), \
             patch("backend.chat_agent_agentic._build_graph", return_value=mock_app), \
             patch.object(app_module, "_load_catalog", return_value=sample_catalog):
            first, status1 = _post(
                f"{test_server}/api/v1/chat",
                {"message": "quiero leche", "history": [], "cart": []},
                headers={"X-Session-Token": "sess-header"},
            )
            second, status2 = _post(
                f"{test_server}/api/v1/chat",
                {
                    "message": "Leche entera La Serenísima 1L.",
                    "history": [{"role": "assistant", "content": first["data"]["reply"]}],
                    "cart": [],
                    "clarification_response": {
                        "pending_request_id": "pending-1",
                        "chosen_option_id": "p1",
                    },
                },
                headers={"X-Session-Token": "sess-header"},
            )

        assert status1 == 200
        assert status2 == 200
        assert first["data"]["clarification"] is not None
        assert second["data"]["clarification"] is None
        assert second["data"]["cart"][0]["product_id"] == "p1"
        # Clarification resolution no longer re-invokes the graph (loop fix).
        assert mock_app.invoke.call_count == 1

    def test_invalid_clarification_reply_does_not_consume_pending_request(self, test_server, sample_catalog):
        from backend.chat_agent_agentic import _reset_app_cache

        _reset_app_cache()
        clarification = {
            "question": "¿Cuál leche querés?",
            "options": [
                {"id": "p1", "label": "Leche entera La Serenísima 1L", "product": sample_catalog[0]},
            ],
            "pending_request_id": "pending-1",
        }
        pending_state = MagicMock()
        pending_state.values = {"pending_clarification": clarification}

        mock_app = MagicMock()
        mock_app.get_state.return_value = pending_state
        mock_app.invoke.return_value = _graph_state(reply="¿Cuál leche querés?", clarification=clarification)

        import backend.app as app_module
        with patch("backend.chat_agent_agentic._load_catalog", return_value=sample_catalog), \
             patch("backend.chat_agent_agentic._build_graph", return_value=mock_app), \
             patch.object(app_module, "_load_catalog", return_value=sample_catalog):
            first, status1 = _post(
                f"{test_server}/api/v1/chat",
                {"message": "quiero leche", "history": [], "cart": []},
                headers={"X-Session-Token": "sess-invalid-clar"},
            )
            invalid, status2 = _post(
                f"{test_server}/api/v1/chat",
                {
                    "message": "otra cualquiera",
                    "history": [{"role": "assistant", "content": first["data"]["reply"]}],
                    "cart": [],
                    "clarification_response": {
                        "pending_request_id": "pending-1",
                        "chosen_option_id": "p2",
                    },
                },
                headers={"X-Session-Token": "sess-invalid-clar"},
            )
            valid, status3 = _post(
                f"{test_server}/api/v1/chat",
                {
                    "message": "Leche entera La Serenísima 1L",
                    "history": [{"role": "assistant", "content": first["data"]["reply"]}],
                    "cart": [],
                    "clarification_response": {
                        "pending_request_id": "pending-1",
                        "chosen_option_id": "p1",
                    },
                },
                headers={"X-Session-Token": "sess-invalid-clar"},
            )

        assert status1 == 200
        assert status2 == 200
        assert status3 == 200
        assert invalid["data"]["clarification"]["pending_request_id"] == "pending-1"
        assert "no es válida" in invalid["data"]["reply"].lower()
        assert valid["data"]["clarification"] is None
        assert valid["data"]["cart"][0]["product_id"] == "p1"
        _reset_app_cache()


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


# ─── V4: session cart endpoints ───────────────────────────────────────────────

class TestSessionCartEndpoints:
    def test_get_empty_cart(self, test_server):
        result = _get(f"{test_server}/api/v1/cart?session_id=sess-1")
        assert result["ok"] is True
        assert result["data"]["session_id"] == "sess-1"
        assert result["data"]["items"] == []

    def test_get_without_session_id_returns_400(self, test_server):
        from urllib.error import HTTPError
        try:
            _get(f"{test_server}/api/v1/cart")
            assert False, "expected error"
        except HTTPError as e:
            assert e.code == 400

    def test_add_item_to_cart(self, test_server):
        result, status = _post(f"{test_server}/api/v1/cart", {
            "session_id": "sess-2", "product_id": "p1", "quantity": 3
        })
        assert status == 200
        assert result["ok"] is True
        assert result["data"]["quantity"] == 3

    def test_get_cart_after_add(self, test_server):
        _post(f"{test_server}/api/v1/cart", {"session_id": "sess-3", "product_id": "p42", "quantity": 2})
        result = _get(f"{test_server}/api/v1/cart?session_id=sess-3")
        items = result["data"]["items"]
        assert len(items) == 1
        assert items[0]["product_id"] == "p42"
        assert items[0]["quantity"] == 2

    def test_add_updates_quantity_on_duplicate(self, test_server):
        _post(f"{test_server}/api/v1/cart", {"session_id": "sess-4", "product_id": "p1", "quantity": 1})
        _post(f"{test_server}/api/v1/cart", {"session_id": "sess-4", "product_id": "p1", "quantity": 5})
        result = _get(f"{test_server}/api/v1/cart?session_id=sess-4")
        assert result["data"]["items"][0]["quantity"] == 5

    def test_remove_item_from_cart(self, test_server):
        _post(f"{test_server}/api/v1/cart", {"session_id": "sess-5", "product_id": "p1", "quantity": 1})
        result, status = _post(f"{test_server}/api/v1/cart/remove", {
            "session_id": "sess-5", "product_id": "p1"
        })
        assert status == 200
        assert result["data"]["removed"] is True
        items = _get(f"{test_server}/api/v1/cart?session_id=sess-5")["data"]["items"]
        assert items == []

    def test_add_missing_session_id_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/cart", {"product_id": "p1", "quantity": 1})
        assert status == 400
        assert result["ok"] is False

    def test_add_missing_product_id_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/cart", {"session_id": "sess-6", "quantity": 1})
        assert status == 400
        assert result["ok"] is False

    def test_carts_are_isolated_by_session(self, test_server):
        _post(f"{test_server}/api/v1/cart", {"session_id": "sess-a", "product_id": "p1", "quantity": 1})
        _post(f"{test_server}/api/v1/cart", {"session_id": "sess-b", "product_id": "p2", "quantity": 2})
        items_a = _get(f"{test_server}/api/v1/cart?session_id=sess-a")["data"]["items"]
        items_b = _get(f"{test_server}/api/v1/cart?session_id=sess-b")["data"]["items"]
        assert len(items_a) == 1 and items_a[0]["product_id"] == "p1"
        assert len(items_b) == 1 and items_b[0]["product_id"] == "p2"


# ─── V4: recurring plan endpoints ─────────────────────────────────────────────

class TestRecurringPlanEndpoints:
    def test_get_empty_plan(self, test_server):
        result = _get(f"{test_server}/api/v1/recurring-plan")
        assert result["ok"] is True
        assert result["data"]["plan"] == {}

    def test_save_and_retrieve_plan(self, test_server):
        plan = {
            "household_size": 3,
            "monthly_budget": 50000.0,
            "priority_items": ["p1", "p2"],
            "preferred_brands": {"leche": "La Serenísima"},
            "strict_brand": True,
            "excluded_categories": ["alcohol"],
            "notes": "sin tacc",
        }
        result, status = _post(f"{test_server}/api/v1/recurring-plan", {"plan": plan})
        assert status == 200
        assert result["ok"] is True
        assert result["data"]["plan"]["household_size"] == 3
        assert result["data"]["plan"]["monthly_budget"] == 50000.0
        assert result["data"]["plan"]["priority_items"] == ["p1", "p2"]
        assert result["data"]["plan"]["strict_brand"] is True

    def test_get_plan_after_save(self, test_server):
        _post(f"{test_server}/api/v1/recurring-plan", {"plan": {"household_size": 2, "notes": "vegano"}})
        result = _get(f"{test_server}/api/v1/recurring-plan")
        assert result["data"]["plan"]["household_size"] == 2
        assert result["data"]["plan"]["notes"] == "vegano"

    def test_upsert_updates_existing(self, test_server):
        _post(f"{test_server}/api/v1/recurring-plan", {"plan": {"household_size": 2}})
        _post(f"{test_server}/api/v1/recurring-plan", {"plan": {"household_size": 5}})
        result = _get(f"{test_server}/api/v1/recurring-plan")
        assert result["data"]["plan"]["household_size"] == 5

    def test_non_dict_plan_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/recurring-plan", {"plan": "bad"})
        assert status == 400
        assert result["ok"] is False


# ─── V4: recurring plan generate / accept ─────────────────────────────────────

class TestRecurringPlanGenerateAccept:
    def test_generate_returns_proposed_cart(self, test_server):
        result, status = _post(f"{test_server}/api/v1/recurring-plan/generate", {})
        assert status == 200
        assert result["ok"] is True
        assert "proposed_cart" in result["data"]
        assert isinstance(result["data"]["proposed_cart"], list)

    def test_generate_with_no_catalog_returns_empty_cart(self, test_server):
        import backend.app as app_module
        from unittest.mock import patch
        with patch.object(app_module, "_load_catalog", return_value=[]):
            result, status = _post(f"{test_server}/api/v1/recurring-plan/generate", {})
        assert status == 200
        assert result["data"]["proposed_cart"] == []

    def test_accept_non_list_returns_400(self, test_server):
        result, status = _post(f"{test_server}/api/v1/recurring-plan/accept", {"proposed_cart": "bad"})
        assert status == 400
        assert result["ok"] is False

    def test_accept_saves_as_order(self, test_server, sample_catalog):
        import backend.app as app_module
        from unittest.mock import patch
        cart = [{"product_id": "p1", "quantity": 2}, {"product_id": "p2", "quantity": 1}]
        with patch.object(app_module, "_load_catalog", return_value=sample_catalog):
            result, status = _post(f"{test_server}/api/v1/recurring-plan/accept", {"proposed_cart": cart})
        assert status == 200
        assert result["ok"] is True
        assert "order_id" in result["data"]
        assert result["data"]["total"] == round(350.0 * 2 + 180.0 * 1, 2)

    def test_accept_empty_cart_saves_zero_order(self, test_server):
        result, status = _post(f"{test_server}/api/v1/recurring-plan/accept", {"proposed_cart": []})
        assert status == 200
        assert result["data"]["total"] == 0.0
        assert result["data"]["items"] == 0
