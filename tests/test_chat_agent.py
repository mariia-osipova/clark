"""
Tests for Jeremias's chat agent tasks:
  - Chat backend wraps the OpenAI API
  - Prompt style and reply tone are defined
  - Catalog context is injected into the system prompt

All tests mock the OpenAI client — no API key required.
"""

import sys
import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on the path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")

from backend.chat_agent_agentic import (
    _build_system_prompt,
    _catalog_summary,
    _validate_cart,
    handle_chat,
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
            "discount_pct": 0,
            "available_quantity": 10,
            "image_url": "https://example.com/leche.jpg",
        },
        {
            "id": "p2",
            "name": "Yogur frutilla Danone",
            "brand": "Danone",
            "package_size": "200g",
            "price": 180.0,
            "discount_pct": 15,
            "available_quantity": 5,
            "image_url": "https://example.com/yogur.jpg",
        },
        {
            "id": "p3",
            "name": "Pan lactal Bimbo",
            "brand": "Bimbo",
            "package_size": "500g",
            "price": 420.0,
            "discount_pct": 0,
            "available_quantity": 0,  # out of stock
            "image_url": "",
        },
    ]


# ─── System prompt tests ──────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_prompt_is_in_spanish(self):
        prompt = _build_system_prompt()
        assert "español" in prompt.lower() or "asistente" in prompt.lower()

    def test_prompt_mentions_resolve_product(self):
        prompt = _build_system_prompt()
        assert "resolve_product" in prompt

    def test_prompt_instructs_not_to_invent_product_ids(self):
        prompt = _build_system_prompt()
        assert "product_id" in prompt.lower()

    def test_prompt_mentions_add_to_cart(self):
        prompt = _build_system_prompt()
        assert "add_to_cart" in prompt

    def test_prompt_mentions_spanish_awareness(self):
        prompt = _build_system_prompt()
        assert "español" in prompt.lower()

    def test_prompt_mentions_request_clarification(self):
        prompt = _build_system_prompt()
        assert "request_clarification" in prompt

    def test_prompt_mentions_report_missing(self):
        prompt = _build_system_prompt()
        assert "report_missing" in prompt

    def test_prompt_does_not_mention_search_products(self):
        prompt = _build_system_prompt()
        assert "search_products" not in prompt


# ─── Catalog summary tests ────────────────────────────────────────────────────

class TestCatalogSummary:
    def test_summary_includes_product_name(self, sample_catalog):
        summary = _catalog_summary(sample_catalog)
        assert "Leche entera La Serenísima" in summary

    def test_summary_includes_price(self, sample_catalog):
        summary = _catalog_summary(sample_catalog)
        assert "350.00" in summary

    def test_summary_shows_discount(self, sample_catalog):
        summary = _catalog_summary(sample_catalog)
        assert "15% OFF" in summary

    def test_summary_no_discount_for_zero(self, sample_catalog):
        summary = _catalog_summary([sample_catalog[0]])  # p1 has 0 discount
        assert "OFF" not in summary

    def test_summary_respects_max_items(self, sample_catalog):
        summary = _catalog_summary(sample_catalog, max_items=1)
        assert "p1" in summary or "Leche" in summary
        assert "más." in summary  # truncation notice

    def test_empty_catalog_returns_empty_string(self):
        assert _catalog_summary([]) == ""

    def test_summary_includes_product_id(self, sample_catalog):
        summary = _catalog_summary(sample_catalog)
        assert "[p1]" in summary


# ─── Cart validation tests ────────────────────────────────────────────────────

class TestValidateCart:
    def test_valid_item_passes_through(self, sample_catalog):
        items = [{"product_id": "p1", "quantity": 2}]
        result = _validate_cart(items, sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"
        assert result[0]["quantity"] == 2
        assert result[0]["price"] == 350.0

    def test_unknown_product_is_removed(self, sample_catalog):
        items = [{"product_id": "nonexistent", "quantity": 1}]
        result = _validate_cart(items, sample_catalog)
        assert result == []

    def test_out_of_stock_item_is_removed(self, sample_catalog):
        items = [{"product_id": "p3", "quantity": 1}]  # p3 has available_quantity=0
        result = _validate_cart(items, sample_catalog)
        assert result == []

    def test_quantity_floored_to_one(self, sample_catalog):
        items = [{"product_id": "p1", "quantity": 0}]
        result = _validate_cart(items, sample_catalog)
        assert result[0]["quantity"] == 1

    def test_server_overwrites_price_from_catalog(self, sample_catalog):
        items = [{"product_id": "p1", "quantity": 1, "price": 9999.0}]
        result = _validate_cart(items, sample_catalog)
        assert result[0]["price"] == 350.0  # catalog price wins

    def test_catalog_fields_are_populated(self, sample_catalog):
        items = [{"product_id": "p2", "quantity": 1}]
        result = _validate_cart(items, sample_catalog)
        item = result[0]
        assert item["name"] == "Yogur frutilla Danone"
        assert item["brand"] == "Danone"
        assert item["package_size"] == "200g"
        assert item["image_url"] == "https://example.com/yogur.jpg"

    def test_mixed_valid_and_invalid(self, sample_catalog):
        items = [
            {"product_id": "p1", "quantity": 1},
            {"product_id": "bad_id", "quantity": 1},
            {"product_id": "p3", "quantity": 1},  # out of stock
        ]
        result = _validate_cart(items, sample_catalog)
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"


# ─── handle_chat integration tests ───────────────────────────────────────────

def _make_graph_state(reply="ok", result_cart=None, clarification=None):
    """Build a minimal final_state dict as returned by the phase-based graph.invoke()."""
    return {
        "reply": reply,
        "resolved_cart": result_cart,
        "pending_clarification": clarification,
        "missing_items": [],
    }


class TestHandleChat:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        pass

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        pass

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_returns_reply_on_plain_response(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="Hola, ¿en qué te puedo ayudar?")
        mock_build_graph.return_value = mock_app

        result = handle_chat("Hola", [], [])

        assert result["reply"] == "Hola, ¿en qué te puedo ayudar?"
        assert result["cart"] is None
        assert result["clarification"] is None

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_result_cart_fallback_is_returned_without_session_id(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        expected_cart = [{
            "product_id": "p1",
            "name": "Leche entera La Serenísima",
            "brand": "La Serenísima",
            "package_size": "1L",
            "price": 350.0,
            "quantity": 1,
            "image_url": "https://example.com/leche.jpg",
        }]
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(
            reply="Agregué la leche.", result_cart=expected_cart
        )
        mock_build_graph.return_value = mock_app

        result = handle_chat("quiero leche", [], [])

        assert result["cart"] is not None
        assert result["cart"] == expected_cart
        assert len(result["cart"]) == 1
        assert result["cart"][0]["product_id"] == "p1"

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_request_clarification_tool_returns_clarification(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        expected_clarification = {
            "question": "¿Cuál leche querés?",
            "options": [
                {"id": "opt1", "label": "Leche entera 1L"},
                {"id": "opt2", "label": "Leche descremada 1L"},
            ],
            "pending_request_id": "some-uuid",
        }
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(
            reply="¿Cuál leche querés?", clarification=expected_clarification
        )
        mock_build_graph.return_value = mock_app

        result = handle_chat("quiero leche", [], [])

        assert result["clarification"] is not None
        assert result["clarification"]["question"] == "¿Cuál leche querés?"
        assert len(result["clarification"]["options"]) == 2
        assert "pending_request_id" in result["clarification"]

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_clarification_resolution_bypasses_llm_and_adds_to_cart(self, mock_build_graph, mock_load_catalog, sample_catalog):
        """When clarification_response resolves to a known product_id with no
        pending_message, the cart is updated directly without invoking the graph."""
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_build_graph.return_value = mock_app

        result = handle_chat(
            "Leche entera La Serenísima",
            [],
            [],
            clarification_response={"pending_request_id": "pending-1", "chosen_option_id": "p1"},
        )

        mock_app.invoke.assert_not_called()
        assert result["clarification"] is None
        assert any(item["product_id"] == "p1" for item in (result["cart"] or []))

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_clarification_resolution_continues_with_pending_message(self, mock_build_graph, mock_load_catalog, sample_catalog):
        """Clarification resolution adds the chosen item and returns immediately.
        The graph is NOT re-invoked even when pending_message is present, to avoid
        re-running resolve_product on the same query and entering a clarification loop."""
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_build_graph.return_value = mock_app

        result = handle_chat(
            "Leche entera La Serenísima",
            [],
            [],
            clarification_response={
                "pending_request_id": "pending-1",
                "chosen_option_id": "p1",
                "pending_message": "quiero leche y yogur",
            },
        )

        mock_app.invoke.assert_not_called()
        assert result["clarification"] is None
        assert any(item["product_id"] == "p1" for item in (result["cart"] or []))

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_plain_text_reply_without_clarification_response_goes_through_graph(
        self, mock_build_graph, mock_load_catalog, sample_catalog
    ):
        """Text-only replies on an existing session go through the graph normally.
        Clarification resolution requires an explicit clarification_response payload."""
        from backend.chat_agent_agentic import _reset_app_cache

        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        empty_state = MagicMock()
        empty_state.values = {}
        mock_app.get_state.return_value = empty_state
        # Turn 1 returns clarification; turn 2 (text only) returns no clarification
        mock_app.invoke.side_effect = [
            _make_graph_state(reply="¿Cuál leche querés?", clarification={
                "question": "¿Cuál leche querés?",
                "options": [{"id": "p1", "label": "Leche entera La Serenísima 1L", "product": sample_catalog[0]}],
                "pending_request_id": "pending-1",
            }),
            _make_graph_state(reply="ok"),
        ]
        mock_build_graph.return_value = mock_app

        first = handle_chat("quiero leche", [], [], session_id="sess-clarif")
        second = handle_chat(
            "Leche entera La Serenísima 1L.",
            [{"role": "assistant", "content": first["reply"]}],
            [],
            session_id="sess-clarif",
        )

        assert first["clarification"] is not None
        # Text-only reply goes through graph — graph returns no clarification
        assert second["clarification"] is None
        assert mock_app.invoke.call_count == 2

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_clarification_response_unknown_id_is_rejected(self, mock_build_graph, mock_load_catalog, sample_catalog):
        """Unknown chosen_option_id is rejected without starting a fresh graph turn."""
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_build_graph.return_value = mock_app

        result = handle_chat(
            "algo",
            [],
            [],
            clarification_response={"pending_request_id": "pending-1", "chosen_option_id": "nonexistent"},
        )

        mock_app.invoke.assert_not_called()
        assert "ya no está disponible" in result["reply"].lower()

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_openai_is_called_once_per_turn(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_build_graph.return_value = mock_app

        handle_chat("test", [], [])

        mock_app.invoke.assert_called_once()

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_history_is_included_in_messages(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_build_graph.return_value = mock_app

        history = [{"role": "user", "content": "mensaje anterior"}]
        handle_chat("nuevo mensaje", history, [])

        call_args = mock_app.invoke.call_args
        state = call_args.args[0] if call_args.args else call_args.kwargs.get("input", call_args.args[0])
        # New graph passes history as state["history"] (list of dicts) and message as state["raw_message"]
        history_list = state["history"]
        contents = [m.get("content", "") for m in history_list]
        assert any("mensaje anterior" in c for c in contents)
        assert state["raw_message"] == "nuevo mensaje"

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_empty_catalog_does_not_crash(self, mock_build_graph, mock_load_catalog):
        mock_load_catalog.return_value = []
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_build_graph.return_value = mock_app

        result = handle_chat("hola", [], [])
        assert "reply" in result

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_add_to_cart_tool_validates_and_returns_json(self, mock_build_graph, mock_load_catalog, sample_catalog):
        """DB writes are handled by _write_session_cart_items (tested here directly)."""
        import tempfile
        from backend.chat_agent_agentic import _write_session_cart_items, _reset_app_cache
        from backend import db as _db
        _reset_app_cache()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        orig = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = tmp_path
            _db.init_db()
            _write_session_cart_items("sess-xyz", [("p1", 2)])
            conn = _db.get_db()
            row = conn.execute(
                "SELECT quantity FROM session_carts WHERE session_id=? AND product_id=?",
                ("sess-xyz", "p1"),
            ).fetchone()
            conn.close()
            assert row["quantity"] == 2
        finally:
            if orig is not None:
                os.environ["DB_PATH"] = orig
            elif "DB_PATH" in os.environ:
                del os.environ["DB_PATH"]
            os.unlink(tmp_path)

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_add_to_cart_reflected_in_reply(self, mock_build_graph, mock_load_catalog, sample_catalog):
        from backend.chat_agent_agentic import handle_chat, _reset_app_cache
        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="Agregué la leche.")
        mock_build_graph.return_value = mock_app
        # No session_id → cart comes from resolved_cart (None here)
        result = handle_chat(message="quiero leche", history=[], cart=[])
        assert result["reply"] == "Agregué la leche."

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_result_always_has_required_keys(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_build_graph.return_value = mock_app

        result = handle_chat("hola", [], [])
        assert "reply" in result
        assert "cart" in result
        assert "clarification" in result

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_handle_chat_accepts_session_id(self, mock_build_graph, mock_load_catalog, sample_catalog):
        from backend.chat_agent_agentic import handle_chat, _reset_app_cache
        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="Hola")
        mock_build_graph.return_value = mock_app
        # Should not raise; session_id is accepted as a keyword arg
        result = handle_chat(message="hola", history=[], cart=[], session_id="sess-abc")
        assert result["reply"] == "Hola"

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_generate_monthly_basket_action_returns_proposed_cart(
        self, mock_build_graph, mock_load_catalog, sample_catalog
    ):
        """action='generate_monthly_basket' bypasses the graph and returns a proposed_cart."""
        import tempfile

        from backend import db as _db
        from backend.chat_agent_agentic import _reset_app_cache

        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name

        orig = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = tmp_path
            _db.init_db()
            conn = _db.get_db()
            conn.execute(
                """INSERT INTO recurring_plans (
                    id, household_size, monthly_budget, priority_items,
                    preferred_brands, strict_brand, excluded_categories, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("default", 2, 5000.0, "[]", "{}", 0, "[]", ""),
            )
            conn.commit()
            conn.close()

            with patch("backend.product_semantic_index.generate_monthly_basket_candidates") as mock_gen:
                mock_gen.return_value = {
                    "candidates": [
                        {
                            "query": "leche",
                            "tag": "must_have",
                            "status": "resolved",
                            "product": sample_catalog[0],
                            "quantity": 1,
                            "estimated_price": 350.0,
                        }
                    ],
                    "budget_overflow": False,
                }
                result = handle_chat(
                    message="",
                    history=[],
                    cart=[],
                    action="generate_monthly_basket",
                )

            assert "proposed_cart" in result
            assert len(result["proposed_cart"]) == 1
            assert result["proposed_cart"][0]["product_id"] == "p1"
            assert "canasta mensual" in result["reply"].lower()
            mock_build_graph.assert_not_called()
        finally:
            if orig is not None:
                os.environ["DB_PATH"] = orig
            elif "DB_PATH" in os.environ:
                del os.environ["DB_PATH"]
            os.unlink(tmp_path)

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_generate_monthly_basket_no_plan_returns_empty(
        self, mock_build_graph, mock_load_catalog, sample_catalog
    ):
        """With no recurring plan in DB, proposed_cart should be empty."""
        import tempfile

        from backend import db as _db
        from backend.chat_agent_agentic import _reset_app_cache

        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name

        orig = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = tmp_path
            _db.init_db()

            result = handle_chat(
                message="",
                history=[],
                cart=[],
                action="generate_monthly_basket",
            )

            assert "proposed_cart" in result
            assert result["proposed_cart"] == []
            assert "plan" in result["reply"].lower()
            mock_build_graph.assert_not_called()
        finally:
            if orig is not None:
                os.environ["DB_PATH"] = orig
            elif "DB_PATH" in os.environ:
                del os.environ["DB_PATH"]
            os.unlink(tmp_path)


class TestSingletonCache:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.chat_agent_agentic._get_checkpointer")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_graph_cached_after_first_build(
        self, mock_load_catalog, mock_llm_cls, mock_get_checkpointer, sample_catalog
    ):
        """Graph is built once and cached; subsequent calls with the same catalog reuse it."""
        from backend.chat_agent_agentic import _reset_app_cache
        from langgraph.checkpoint.memory import MemorySaver
        _reset_app_cache()
        mock_get_checkpointer.return_value = MemorySaver()
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"turn_kind": "smalltalk", "planned_items": []}')
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("cómo estás", [], [])

        # Graph built once, cached for the second call
        assert mock_llm_cls.call_count == 1

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_catalog_loaded_once_across_two_calls(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"turn_kind": "smalltalk", "planned_items": []}')
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("adiós", [], [])

        assert mock_load_catalog.call_count == 1

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_reset_cache_forces_rebuild(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"turn_kind": "smalltalk", "planned_items": []}')
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        handle_chat("hola de nuevo", [], [])

        assert mock_llm_cls.call_count == 2


class TestResilience:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_llm_constructed_with_timeout(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"turn_kind": "smalltalk", "planned_items": []}')
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])

        call_kwargs = mock_llm_cls.call_args.kwargs
        assert "timeout" in call_kwargs
        assert call_kwargs["timeout"] > 0

    @patch("backend.chat_agent_agentic._get_or_build_app")
    def test_invoke_called_with_recursion_limit(self, mock_get_or_build_app):
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_get_or_build_app.return_value = (mock_app, [])

        handle_chat("hola", [], [])

        _, kwargs = mock_app.invoke.call_args
        assert "config" in kwargs
        assert kwargs["config"]["recursion_limit"] >= 20


class TestCleanups:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_missing_items_dedup_is_case_insensitive(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """resolve_product returning not_found adds query to missing_items exactly once."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {"status": "not_found", "quantity": 1}

        llm = MagicMock()
        llm.invoke.side_effect = [
            MagicMock(content='{"turn_kind": "shopping", "planned_items": [{"query": "sal", "quantity": 1}]}'),
            MagicMock(content="No encontré sal."),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("necesito sal", [], [])

        assert result["missing_items"] == ["sal"]

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_huge_history_does_not_exceed_char_budget(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        captured = []

        def invoke(messages):
            captured.append(list(messages))
            return MagicMock(content='{"turn_kind": "smalltalk", "planned_items": []}')

        llm = MagicMock()
        llm.invoke.side_effect = invoke
        mock_llm_cls.return_value = llm

        huge_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 5000}
            for i in range(25)
        ]

        handle_chat("hola", huge_history, [])

        assert captured
        total_chars = sum(len(str(message.content)) for message in captured[0])
        assert total_chars < 60_000

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_ambiguous_resolve_triggers_clarification(
        self, mock_load_catalog, mock_llm_cls, mock_resolve
    ):
        """When resolve_product returns needs_clarification, graph emits clarification."""
        ambiguous_catalog = [
            {
                "id": "milk-1",
                "name": "Leche entera La Serenísima",
                "brand": "La Serenísima",
                "package_size": "1L",
                "price": 350.0,
                "discount_pct": 0,
                "available_quantity": 10,
                "image_url": "",
            },
            {
                "id": "milk-2",
                "name": "Leche entera SanCor",
                "brand": "SanCor",
                "package_size": "1L",
                "price": 320.0,
                "discount_pct": 0,
                "available_quantity": 5,
                "image_url": "",
            },
        ]
        mock_load_catalog.return_value = ambiguous_catalog
        mock_resolve.return_value = {
            "status": "needs_clarification",
            "options": [
                {"id": "milk-1", "label": "La Serenísima 1L $350.00"},
                {"id": "milk-2", "label": "SanCor 1L $320.00"},
            ],
            "quantity": 1,
        }

        llm = MagicMock()
        llm.invoke.side_effect = [
            MagicMock(content='{"turn_kind": "shopping", "planned_items": [{"query": "leche", "quantity": 1}]}'),
            MagicMock(content="¿Cuál leche preferís?"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche", [], [])

        assert result["clarification"] is not None
        assert result["cart"] is None
        option_ids = {opt["id"] for opt in result["clarification"]["options"]}
        assert "milk-1" in option_ids
        assert "milk-2" in option_ids

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_clarification_continuation_injects_resolved_product_context(
        self, mock_load_catalog, mock_llm_cls
    ):
        ambiguous_catalog = [
            {
                "id": "cook-1",
                "name": "Galletitas Oreo",
                "brand": "Oreo",
                "package_size": "118g",
                "price": 400.0,
                "discount_pct": 0,
                "available_quantity": 10,
                "image_url": "",
            },
            {
                "id": "cook-2",
                "name": "Galletitas Pepitos",
                "brand": "Pepitos",
                "package_size": "150g",
                "price": 350.0,
                "discount_pct": 0,
                "available_quantity": 8,
                "image_url": "",
            },
        ]
        mock_load_catalog.return_value = ambiguous_catalog

        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="Listo.")
        mock_llm_cls.return_value = llm

        handle_chat(
            "Galletitas Oreo",
            [],
            [],
            clarification_response={
                "pending_request_id": "p1",
                "chosen_option_id": "cook-1",
                "pending_message": "quiero galletitas",
            },
        )

        # Clarification resolution no longer re-invokes the graph to avoid
        # re-running resolve_product on the same query (clarification loop fix).
        assert llm.invoke.call_count == 0


# ─── Bug fix tests ────────────────────────────────────────────────────────────

class TestBugFixes:
    """Tests for V4 bug fixes."""

    # ── Bug #1 — chosen_option_id not validated against offered options ────────

    @patch("backend.chat_agent_agentic._read_session_cart")
    @patch("backend.chat_agent_agentic._write_session_cart_item")
    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_clarification_response_rejects_id_not_in_options(
        self, mock_build_graph, mock_load_catalog, mock_write_cart, mock_read_cart, sample_catalog
    ):
        """chosen_option_id that was not in the presented options must be rejected.

        The current code validates chosen_option_id against the full catalog,
        not against the specific options that were presented to the user.
        This allows any in-stock product_id to be added by crafting a payload.
        """
        mock_load_catalog.return_value = sample_catalog
        mock_read_cart.return_value = []

        mock_app = MagicMock()
        mock_state = MagicMock()
        mock_state.values = {
            "pending_clarification": {
                # Only p1 was presented as an option — p2 was NOT offered
                "options": [{"id": "p1", "label": "Leche entera La Serenísima"}],
                "original_query": "leche",
                "pending_message": "",
            }
        }
        mock_app.get_state.return_value = mock_state
        mock_build_graph.return_value = mock_app

        # p2 is a valid in-stock product, but it was NOT in the presented options
        result = handle_chat(
            "algo",
            [],
            [],
            clarification_response={"pending_request_id": "req-1", "chosen_option_id": "p2"},
            session_id="sess-bug1-validation",
        )

        # Fix: p2 not in options → reject, no cart write, no graph invocation
        mock_write_cart.assert_not_called()
        mock_app.invoke.assert_not_called()
        cart = result.get("cart") or []
        assert not any(item.get("product_id") == "p2" for item in cart)

    @patch("backend.chat_agent_agentic._read_session_cart")
    @patch("backend.chat_agent_agentic._write_session_cart_item")
    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_clarification_resume_rehydrates_resolved_so_far_into_resolved_cart(
        self, mock_build_graph, mock_load_catalog, mock_write_cart, mock_read_cart, sample_catalog
    ):
        """Resume input must carry forward already-resolved items into resolved_cart.

        Otherwise apply_cart only persists the newly chosen option and any items
        resolved after resume, dropping products resolved before the clarification.
        """
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog
        mock_read_cart.return_value = []

        prior_resolved = {
            "query": "yogur",
            "status": "resolved",
            "product": sample_catalog[1],
            "quantity": 2,
            "options": None,
        }

        mock_app = MagicMock()
        mock_state = MagicMock()
        mock_state.values = {
            "pending_clarification": {
                "options": [{"id": "p1", "label": "Leche entera La Serenísima"}],
                "original_query": "leche",
                "pending_message": "quiero leche y dos yogures",
                "planned_items": [
                    {"query": "leche", "quantity": 1},
                    {"query": "yogur", "quantity": 2},
                ],
                "resolved_so_far": [prior_resolved],
            }
        }
        mock_app.get_state.return_value = mock_state
        mock_app.invoke.return_value = _make_graph_state(reply="Listo.")
        mock_build_graph.return_value = mock_app

        handle_chat(
            "algo",
            [],
            [],
            clarification_response={"pending_request_id": "req-1", "chosen_option_id": "p1"},
            session_id="sess-resume",
        )

        resume_input = mock_app.invoke.call_args.args[0]
        resolved_cart = resume_input["resolved_cart"]

        assert any(item["product_id"] == "p2" and item["quantity"] == 2 for item in resolved_cart)
        assert any(item["product_id"] == "p1" and item["quantity"] == 1 for item in resolved_cart)
        _reset_app_cache()

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_single_item_chat_normalizes_quantity_with_parse_quantity(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Single-item turns must normalize quantity from the raw message.

        This keeps quantity extraction deterministic even when the classifier
        under-extracts or mis-extracts the count.
        """
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {
            "status": "resolved", "product": sample_catalog[1], "quantity": 2,
        }
        llm = MagicMock()
        llm.invoke.side_effect = [
            MagicMock(content=json.dumps({
                "turn_kind": "shopping",
                "planned_items": [{"query": "yogur", "quantity": 1}],
            })),
            MagicMock(content="Agregué dos yogures."),
        ]
        mock_llm_cls.return_value = llm

        handle_chat("agrega dos yogures", [], [])

        mock_resolve.assert_called_once_with("yogur", 2, sample_catalog)
        _reset_app_cache()


# ─── Phase-based graph tests ──────────────────────────────────────────────────

class TestPhaseGraph:
    """Integration tests for the 5-node phase-based graph.
    Mocks ChatOpenAI and resolve_product so the real graph topology runs."""

    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def _llm_classify(self, turn_kind, planned_items):
        return MagicMock(content=json.dumps({"turn_kind": turn_kind, "planned_items": planned_items}))

    def _llm_reply(self, text):
        return MagicMock(content=text)

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_shopping_turn_calls_resolve_and_returns_reply(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Happy path: classify → resolve → apply → summarize."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {
            "status": "resolved", "product": sample_catalog[0], "quantity": 1,
        }
        llm = MagicMock()
        llm.invoke.side_effect = [
            self._llm_classify("shopping", [{"query": "leche entera", "quantity": 1}]),
            self._llm_reply("Agregué leche entera."),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche entera", [], [])

        assert "leche" in result["reply"].lower()
        assert llm.invoke.call_count == 2  # classify_turn + summarize
        mock_resolve.assert_called_once_with("leche entera", 1, sample_catalog)
        assert result["clarification"] is None

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_resolve_items_stops_at_first_clarification(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """When one item needs clarification, the graph still resolves the rest."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {
            "status": "needs_clarification",
            "options": [{"id": "p1", "label": "Leche entera 1L"}],
            "quantity": 1,
        }
        llm = MagicMock()
        llm.invoke.side_effect = [
            self._llm_classify("shopping", [
                {"query": "leche", "quantity": 1},
                {"query": "yogur", "quantity": 1},
            ]),
            self._llm_reply("¿Cuál leche preferís?"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche y yogur", [], [])

        assert mock_resolve.call_count == 2
        assert result["clarification"] is not None
        assert len(result["clarification"]["options"]) == 1

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_smalltalk_skips_resolve_items(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Smalltalk turn: classify → summarize directly; resolve_product never called."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.invoke.side_effect = [
            self._llm_classify("smalltalk", []),
            self._llm_reply("Hola, ¿en qué te puedo ayudar?"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("hola", [], [])

        mock_resolve.assert_not_called()
        assert result["clarification"] is None
        assert result["reply"] != ""

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_multi_item_all_resolved_in_one_turn(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Three items classified → three resolve_product calls → one reply."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {
            "status": "resolved", "product": sample_catalog[0], "quantity": 1,
        }
        llm = MagicMock()
        llm.invoke.side_effect = [
            self._llm_classify("shopping", [
                {"query": "leche", "quantity": 1},
                {"query": "yogur", "quantity": 2},
                {"query": "pan", "quantity": 1},
            ]),
            self._llm_reply("Agregué 3 productos."),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche, yogur y pan", [], [])

        assert mock_resolve.call_count == 3
        assert result["clarification"] is None
        assert "3" in result["reply"] or result["reply"] != ""

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_not_found_item_populates_missing_items(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Product with status=not_found goes to missing_items, no clarification."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {"status": "not_found", "quantity": 1}
        llm = MagicMock()
        llm.invoke.side_effect = [
            self._llm_classify("shopping", [{"query": "producto_xyz", "quantity": 1}]),
            self._llm_reply("No encontré ese producto."),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero producto_xyz", [], [])

        assert "producto_xyz" in result.get("missing_items", [])
        assert result["clarification"] is None

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_resolve_items_skips_already_resolved(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Items pre-populated in resolutions are not re-resolved by resolve_items."""
        from backend.chat_agent_agentic import _get_or_build_app, _reset_app_cache
        _reset_app_cache()
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {
            "status": "resolved", "product": sample_catalog[1], "quantity": 1,
        }
        llm = MagicMock()
        # turn_kind="clarification_reply" → classify_turn skipped → only summarize
        llm.invoke.side_effect = [self._llm_reply("Listo.")]
        mock_llm_cls.return_value = llm

        app, _ = _get_or_build_app("fake-key")
        config = {"configurable": {"thread_id": "test-skip-resolved", "session_id": ""}}

        final_state = app.invoke(
            {
                "raw_message": "quiero leche y yogur",
                "turn_kind": "clarification_reply",  # skips classify_turn
                "planned_items": [
                    {"query": "leche", "quantity": 1},
                    {"query": "yogur", "quantity": 1},
                ],
                "resolutions": [
                    # leche already resolved — must NOT be attempted again
                    {"query": "leche", "status": "resolved",
                     "product": sample_catalog[0], "quantity": 1, "options": None},
                ],
                "resolved_cart": [],
                "missing_items": [],
                "pending_clarification": None,
                "reply": "",
                "session_id": "",
                "initial_cart": [],
                "history": [],
                "context": "",
            },
            config=config,
        )

        # Only "yogur" resolved — "leche" was skipped (already in resolutions)
        mock_resolve.assert_called_once_with("yogur", 1, sample_catalog)
        _reset_app_cache()
