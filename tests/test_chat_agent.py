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

from langchain_core.messages import AIMessage as LCAIMessage, HumanMessage as LCHumanMessage

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

def _make_ai_message(content="ok", tool_calls=None):
    """Build a minimal AIMessage-like mock for LangGraph agent node output."""
    from langchain_core.messages import AIMessage
    if tool_calls:
        return AIMessage(content=content or "", tool_calls=tool_calls)
    return AIMessage(content=content)


def _make_graph_state(reply="ok", result_cart=None, clarification=None):
    """Build a minimal final_state dict as returned by graph.invoke()."""
    from langchain_core.messages import AIMessage
    return {
        "messages": [AIMessage(content=reply)],
        "result_cart": result_cart,
        "clarification": clarification,
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
        """When clarification_response includes a pending_message, the graph is
        re-invoked with the original request so remaining items are processed."""
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="También agregué el yogur.")
        mock_build_graph.return_value = mock_app

        handle_chat(
            "Leche entera La Serenísima",
            [],
            [],
            clarification_response={
                "pending_request_id": "pending-1",
                "chosen_option_id": "p1",
                "pending_message": "quiero leche y yogur",
            },
        )

        mock_app.invoke.assert_called_once()
        state = mock_app.invoke.call_args.args[0]
        # result_cart is None — cart lives in DB; context injected via system message
        assert state["result_cart"] is None
        # The continuation message must be the original pending request
        human_messages = [m for m in state["messages"] if isinstance(m, LCHumanMessage)]
        assert human_messages[-1].content == "quiero leche y yogur"
        # A system message must note what was already resolved (mentions product_id p1)
        all_contents = [getattr(m, "content", "") for m in state["messages"]]
        assert any("p1" in c for c in all_contents)

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._build_graph")
    def test_clarification_response_unknown_id_falls_back_to_graph(self, mock_build_graph, mock_load_catalog, sample_catalog):
        """Unknown chosen_option_id falls through to normal graph invocation without crashing."""
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

        mock_app.invoke.assert_called_once()
        assert result["reply"] == "ok"

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
        messages = state["messages"]
        contents = [
            m.content if hasattr(m, "content") else m.get("content", "")
            for m in messages
        ]
        assert any("mensaje anterior" in c for c in contents)
        assert any("nuevo mensaje" in c for c in contents)

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
    def test_add_to_cart_tool_writes_to_db(self, mock_build_graph, mock_load_catalog, sample_catalog):
        """add_to_cart tool should upsert into session_carts and return confirmation."""
        import tempfile
        from backend.chat_agent_agentic import _make_tools, _reset_app_cache
        from backend import db as _db
        _reset_app_cache()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        orig = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = tmp_path
            _db.init_db()
            tools = _make_tools(sample_catalog, session_id="sess-xyz")
            tool = next(t for t in tools if t.name == "add_to_cart")
            result = json.loads(tool.invoke({"product_id": "p1", "quantity": 2}))
            assert result["added"] is True
            assert result["product_id"] == "p1"
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
        mock_app.invoke.return_value = {
            "messages": [LCAIMessage(content="Agregué la leche.")],
            "result_cart": None,
            "clarification": None,
            "missing_items": [],
        }
        mock_build_graph.return_value = mock_app
        # No session_id → cart comes from result_cart (None here)
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
        mock_app.invoke.return_value = {
            "messages": [LCAIMessage(content="Hola")],
            "result_cart": None,
            "clarification": None,
            "missing_items": [],
        }
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
                mock_gen.return_value = [
                    {
                        "query": "leche",
                        "tag": "must_have",
                        "status": "resolved",
                        "product": sample_catalog[0],
                        "quantity": 1,
                        "estimated_price": 350.0,
                    }
                ]
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


class TestAgenticLoop:
    """
    Integration tests for the LangGraph graph topology.
    Mocks ChatOpenAI so the real graph (nodes + edges + tool execution) runs.
    """

    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        pass

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()
        pass

    def _ai_with_tool_call(self, tool_name, tool_args, call_id="call_1"):
        """AIMessage that triggers a tool call."""
        return LCAIMessage(
            content="",
            tool_calls=[{"name": tool_name, "args": tool_args, "id": call_id, "type": "tool_call"}],
        )

    def _ai_reply(self, text):
        """Final AIMessage with no tool calls."""
        return LCAIMessage(content=text)

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_resolve_then_add_to_cart_then_reply(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """Full happy path through the real graph: resolve_product → add_to_cart → reply."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {"status": "resolved", "product": sample_catalog[0], "quantity": 1}

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("resolve_product", {"query": "leche entera 1L", "quantity": 1}, "c1"),
            self._ai_with_tool_call("add_to_cart", {"product_id": "p1", "quantity": 1}, "c2"),
            self._ai_reply("Agregué Leche entera La Serenísima 1L. $350.00"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche entera 1L", [], [])

        assert llm.invoke.call_count == 3
        assert "leche" in result["reply"].lower()

    @patch("backend.product_semantic_index.resolve_product")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_tool_message_injected_after_resolve(
        self, mock_load_catalog, mock_llm_cls, mock_resolve, sample_catalog
    ):
        """After resolve_product, the second LLM call must receive a ToolMessage."""
        mock_load_catalog.return_value = sample_catalog
        mock_resolve.return_value = {"status": "resolved", "product": sample_catalog[0], "quantity": 1}

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("resolve_product", {"query": "leche"}, "c1"),
            self._ai_reply("ok"),
        ]
        mock_llm_cls.return_value = llm

        handle_chat("quiero leche", [], [])

        second_call_messages = llm.invoke.call_args_list[1][0][0]
        from langchain_core.messages import ToolMessage
        tool_msgs = [m for m in second_call_messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) >= 1

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_plain_reply_one_llm_call(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """Conversational message with no tool calls resolves in one LLM call."""
        mock_load_catalog.return_value = sample_catalog

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = self._ai_reply("Hola, ¿en qué te puedo ayudar?")
        mock_llm_cls.return_value = llm

        result = handle_chat("hola", [], [])

        assert llm.invoke.call_count == 1
        assert result["reply"] == "Hola, ¿en qué te puedo ayudar?"
        assert result["cart"] is None

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_add_to_cart_out_of_stock_returns_error(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        """add_to_cart with an out-of-stock product_id returns added=False."""
        mock_load_catalog.return_value = sample_catalog

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("add_to_cart", {"product_id": "p3", "quantity": 1}, "c1"),
            self._ai_reply("Lo siento, ese producto no está disponible."),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero pan lactal", [], [])

        # No session_id → cart from result_cart (None); reply is set
        assert "disponible" in result["reply"].lower()

    @patch("backend.product_semantic_index.search")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_graph_halts_after_request_clarification(
        self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
    ):
        """Graph must stop immediately after request_clarification — no extra LLM call."""
        mock_load_catalog.return_value = sample_catalog
        mock_search.return_value = [sample_catalog[0]]

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call(
                "request_clarification",
                {
                    "question": "¿Cuál leche querés?",
                    "options": [
                        {"id": "opt1", "label": "Entera"},
                        {"id": "opt2", "label": "Descremada"},
                    ],
                },
                "c1",
            ),
            self._ai_reply("Extra unexpected reply"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche", [], [])

        assert llm.invoke.call_count == 1, (
            f"Expected 1 LLM call, got {llm.invoke.call_count}. "
            "Graph did not halt after request_clarification."
        )
        assert result["clarification"] is not None
        assert result["clarification"]["question"] == "¿Cuál leche querés?"
        assert result["reply"] == "¿Cuál leche querés?"

    @patch("backend.product_semantic_index.search")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_clarification_tool_result_contains_pending_id(
        self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
    ):
        """request_clarification must return a pending_request_id in its JSON payload."""
        mock_load_catalog.return_value = sample_catalog
        mock_search.return_value = [sample_catalog[0]]

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = self._ai_reply("ok")
        mock_llm_cls.return_value = llm

        from backend.chat_agent_agentic import _make_tools

        tools = _make_tools(sample_catalog)
        req_clarification = next(t for t in tools if t.name == "request_clarification")
        result_json = req_clarification.invoke(
            {"question": "¿Cuál?", "options": [{"id": "o1", "label": "A"}]}
        )
        result = json.loads(result_json)

        assert "pending_request_id" in result
        assert result["pending_request_id"]

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_add_to_cart_unknown_product_returns_error_in_tool_message(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        """When add_to_cart receives an unknown product_id, the ToolMessage must report added=False."""
        mock_load_catalog.return_value = sample_catalog

        captured_tool_messages = []
        invoke_count = 0

        def invoke(messages):
            nonlocal invoke_count
            from langchain_core.messages import ToolMessage

            if invoke_count == 0:
                invoke_count += 1
                return self._ai_with_tool_call(
                    "add_to_cart",
                    {"product_id": "bad_id", "quantity": 1},
                    "c1",
                )
            captured_tool_messages.extend(
                [message for message in messages if isinstance(message, ToolMessage)]
            )
            return self._ai_reply("ok")

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = invoke
        mock_llm_cls.return_value = llm

        handle_chat("poneme algo inexistente", [], [])

        assert len(captured_tool_messages) == 1
        result = json.loads(captured_tool_messages[0].content)
        assert result.get("added") is False
        assert "error" in result


class TestSingletonCache:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_graph_built_per_call(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        """Graph is rebuilt each call (session_id may differ); catalog is cached separately."""
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = LCAIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("cómo estás", [], [])

        # ChatOpenAI is instantiated once per graph build → two calls now
        assert mock_llm_cls.call_count == 2

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_catalog_loaded_once_across_two_calls(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = LCAIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("adiós", [], [])

        assert mock_load_catalog.call_count == 1

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_reset_cache_forces_rebuild(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = LCAIMessage(content="ok")
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
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = LCAIMessage(content="ok")
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

    def _ai_with_tool_call(self, tool_name, tool_args, call_id="call_1"):
        return LCAIMessage(
            content="",
            tool_calls=[{"name": tool_name, "args": tool_args, "id": call_id, "type": "tool_call"}],
        )

    def _ai_reply(self, text):
        return LCAIMessage(content=text)

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_missing_items_dedup_is_case_insensitive(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("report_missing", {"ingredient": "Sal"}, "c1"),
            self._ai_with_tool_call("report_missing", {"ingredient": "sal"}, "c2"),
            self._ai_reply("ok"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("necesito sal", [], [])

        assert result["missing_items"] == ["sal"]

    @patch("backend.product_semantic_index.resolve_product", side_effect=RuntimeError("SECRET_INTERNAL_PATH=/opt/server"))
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_tool_exception_message_is_sanitized(
        self, mock_load_catalog, mock_llm_cls, _mock_resolve, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        captured = []

        def invoke(messages):
            captured.append(list(messages))
            if len(captured) == 1:
                return self._ai_with_tool_call("resolve_product", {"query": "leche"}, "c1")
            return self._ai_reply("ok")

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = invoke
        mock_llm_cls.return_value = llm

        handle_chat("quiero leche", [], [])

        from langchain_core.messages import ToolMessage

        tool_messages = [m for m in captured[1] if isinstance(m, ToolMessage)]
        assert tool_messages
        for tool_message in tool_messages:
            assert "SECRET_INTERNAL_PATH" not in tool_message.content

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_huge_history_does_not_exceed_char_budget(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        captured = []

        def invoke(messages):
            captured.append(list(messages))
            return self._ai_reply("ok")

        llm = MagicMock()
        llm.bind_tools.return_value = llm
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
        """
        When the LLM calls request_clarification, tools_node must surface it
        and stop the graph.
        """
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
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call(
                "request_clarification",
                {
                    "question": "¿Cuál leche querés?",
                    "options": [
                        {"id": "milk-1", "label": "La Serenísima 1L $350.00"},
                        {"id": "milk-2", "label": "SanCor 1L $320.00"},
                    ],
                },
                "c1",
            ),
            self._ai_reply("Acá tenés tus opciones de leche."),
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
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = self._ai_reply("Listo.")
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

        assert llm.invoke.call_count == 1
        messages = llm.invoke.call_args_list[0][0][0]
        contents = [getattr(message, "content", "") for message in messages]
        assert any("No vuelvas a buscar ese producto" in content for content in contents)
        assert any("quiero galletitas" in content for content in contents)

    def test_search_products_tool_no_longer_exists(self):
        from backend.chat_agent_agentic import _make_tools

        tool_names = [tool.name for tool in _make_tools([])]

        assert "search_products" not in tool_names
        assert "resolve_product" in tool_names
        assert "add_to_cart" in tool_names

    def test_resolve_product_tool_returns_verdict(self, sample_catalog):
        from backend.chat_agent_agentic import _make_tools

        with patch("backend.product_semantic_index.resolve_product") as mock_resolve:
            mock_resolve.return_value = {
                "status": "resolved",
                "product": sample_catalog[0],
                "quantity": 1,
                "substituted": False,
            }

            tool = next(tool for tool in _make_tools(sample_catalog) if tool.name == "resolve_product")
            result = json.loads(tool.invoke({"query": "leche", "quantity": 1}))

        assert result["status"] == "resolved"
        assert result["product"]["id"] == "p1"
