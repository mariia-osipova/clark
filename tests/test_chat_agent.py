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

from langchain_core.messages import AIMessage as LCAIMessage

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

    def test_prompt_instructs_search_before_add(self):
        prompt = _build_system_prompt()
        assert "search_products" in prompt

    def test_prompt_instructs_not_to_hallucinate(self):
        prompt = _build_system_prompt()
        assert "catálogo" in prompt.lower()

    def test_prompt_mentions_cart_tool(self):
        prompt = _build_system_prompt()
        assert "set_cart" in prompt

    def test_prompt_mentions_spanish_awareness(self):
        prompt = _build_system_prompt()
        assert "español" in prompt.lower()

    def test_prompt_mentions_request_clarification(self):
        prompt = _build_system_prompt()
        assert "request_clarification" in prompt

    def test_prompt_clarification_example_has_two_options(self):
        prompt = _build_system_prompt()
        lowered = prompt.lower()
        assert "ambigu" in lowered or "opción" in lowered or "opcion" in lowered


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

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

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
    def test_set_cart_tool_updates_cart(self, mock_build_graph, mock_load_catalog, sample_catalog):
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
    def test_result_always_has_required_keys(self, mock_build_graph, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        mock_app = MagicMock()
        mock_app.invoke.return_value = _make_graph_state(reply="ok")
        mock_build_graph.return_value = mock_app

        result = handle_chat("hola", [], [])
        assert "reply" in result
        assert "cart" in result
        assert "clarification" in result


class TestAgenticLoop:
    """
    Integration tests for the LangGraph graph topology.
    Mocks ChatOpenAI so the real graph (nodes + edges + tool execution) runs.
    """

    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def _ai_with_tool_call(self, tool_name, tool_args, call_id="call_1"):
        """AIMessage that triggers a tool call."""
        return LCAIMessage(
            content="",
            tool_calls=[{"name": tool_name, "args": tool_args, "id": call_id, "type": "tool_call"}],
        )

    def _ai_reply(self, text):
        """Final AIMessage with no tool calls."""
        return LCAIMessage(content=text)

    @patch("backend.product_semantic_index.search")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_search_then_set_cart_then_reply(
        self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
    ):
        """Full happy path through the real graph: search → set_cart → reply."""
        mock_load_catalog.return_value = sample_catalog
        mock_search.return_value = [sample_catalog[0]]  # returns leche

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("search_products", {"query": "leche entera 1L"}, "c1"),
            self._ai_with_tool_call("set_cart", {"items": [{"product_id": "p1", "quantity": 1}]}, "c2"),
            self._ai_reply("Agregué Leche entera La Serenísima 1L. $350.00"),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero leche entera 1L", [], [])

        assert llm.invoke.call_count == 3
        assert result["cart"] is not None
        assert result["cart"][0]["product_id"] == "p1"
        assert "leche" in result["reply"].lower()

    @patch("backend.product_semantic_index.search")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_tool_message_injected_after_search(
        self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
    ):
        """After search_products, the second LLM call must receive a ToolMessage."""
        mock_load_catalog.return_value = sample_catalog
        mock_search.return_value = [sample_catalog[0]]

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("search_products", {"query": "leche"}, "c1"),
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

    @patch("backend.product_semantic_index.search")
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_out_of_stock_removed_from_cart(
        self, mock_load_catalog, mock_llm_cls, mock_search, sample_catalog
    ):
        """set_cart with an out-of-stock product returns empty validated cart."""
        mock_load_catalog.return_value = sample_catalog
        mock_search.return_value = [sample_catalog[2]]  # p3 = out of stock

        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.side_effect = [
            self._ai_with_tool_call("set_cart", {"items": [{"product_id": "p3", "quantity": 1}]}, "c1"),
            self._ai_reply("Lo siento, ese producto no está disponible."),
        ]
        mock_llm_cls.return_value = llm

        result = handle_chat("quiero pan lactal", [], [])

        assert result["cart"] is not None
        assert all(i["product_id"] != "p3" for i in result["cart"])

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
    def test_set_cart_reports_dropped_items(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        """When set_cart drops invalid items, the ToolMessage must list them."""
        mock_load_catalog.return_value = sample_catalog

        captured_tool_messages = []
        invoke_count = 0

        def invoke(messages):
            nonlocal invoke_count
            from langchain_core.messages import ToolMessage

            if invoke_count == 0:
                invoke_count += 1
                return self._ai_with_tool_call(
                    "set_cart",
                    {
                        "items": [
                            {"product_id": "p1", "quantity": 1},
                            {"product_id": "bad_id", "quantity": 1},
                            {"product_id": "p3", "quantity": 1},
                        ]
                    },
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

        handle_chat("poneme leche, cosa_mala y pan", [], [])

        assert len(captured_tool_messages) == 1
        result = json.loads(captured_tool_messages[0].content)
        assert "dropped" in result
        assert len(result["dropped"]) == 2
        dropped_ids = {item["product_id"] for item in result["dropped"]}
        assert dropped_ids == {"bad_id", "p3"}


class TestSingletonCache:
    def setup_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    def teardown_method(self):
        from backend.chat_agent_agentic import _reset_app_cache
        _reset_app_cache()

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_graph_built_once_across_two_calls(
        self, mock_load_catalog, mock_llm_cls, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = LCAIMessage(content="ok")
        mock_llm_cls.return_value = llm

        handle_chat("hola", [], [])
        handle_chat("cómo estás", [], [])

        assert mock_llm_cls.call_count == 1

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

    @patch("backend.product_semantic_index.search", side_effect=RuntimeError("SECRET_INTERNAL_PATH=/opt/server"))
    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_tool_exception_message_is_sanitized(
        self, mock_load_catalog, mock_llm_cls, _mock_search, sample_catalog
    ):
        mock_load_catalog.return_value = sample_catalog
        captured = []

        def invoke(messages):
            captured.append(list(messages))
            if len(captured) == 1:
                return self._ai_with_tool_call("search_products", {"query": "leche"}, "c1")
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
