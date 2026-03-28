"""
Tests for Jeremias's V0 agent tasks:
  - Chat backend wraps the OpenAI API
  - Prompt style and reply tone are defined
  - Catalog context is injected into the system prompt

All tests mock the OpenAI client — no API key required.
"""

import json
import sys
import os
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


def _make_openai_response(content=None, tool_name=None, tool_args=None):
    """Build a minimal mock that mimics openai.ChatCompletion response shape."""
    choice = MagicMock()
    choice.message.content = content

    if tool_name:
        tc = MagicMock()
        tc.function.name = tool_name
        tc.function.arguments = json.dumps(tool_args or {})
        choice.finish_reason = "tool_calls"
        choice.message.tool_calls = [tc]
    else:
        choice.finish_reason = "stop"
        choice.message.tool_calls = None

    response = MagicMock()
    response.choices = [choice]
    return response


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

class TestHandleChat:
    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_returns_reply_on_plain_response(self, mock_openai_fn, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(
            content="Hola, ¿en qué te puedo ayudar?"
        )
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        result = handle_chat("Hola", [], [])

        assert result["reply"] == "Hola, ¿en qué te puedo ayudar?"
        assert result["cart"] is None
        assert result["clarification"] is None

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_set_cart_tool_updates_cart(self, mock_openai_fn, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        client = MagicMock()
        tool_args = {
            "items": [{"product_id": "p1", "name": "Leche", "price": 350.0, "quantity": 1}]
        }
        client.chat.completions.create.return_value = _make_openai_response(
            content="Agregué la leche.",
            tool_name="set_cart",
            tool_args=tool_args,
        )
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        result = handle_chat("quiero leche", [], [])

        assert result["cart"] is not None
        assert len(result["cart"]) == 1
        assert result["cart"][0]["product_id"] == "p1"

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_request_clarification_tool_returns_clarification(self, mock_openai_fn, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        client = MagicMock()
        tool_args = {
            "question": "¿Cuál leche querés?",
            "options": [
                {"id": "opt1", "label": "Leche entera 1L"},
                {"id": "opt2", "label": "Leche descremada 1L"},
            ],
        }
        client.chat.completions.create.return_value = _make_openai_response(
            content="¿Cuál leche querés?",
            tool_name="request_clarification",
            tool_args=tool_args,
        )
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        result = handle_chat("quiero leche", [], [])

        assert result["clarification"] is not None
        assert result["clarification"]["question"] == "¿Cuál leche querés?"
        assert len(result["clarification"]["options"]) == 2
        assert "pending_request_id" in result["clarification"]

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_openai_is_called_once_per_turn(self, mock_openai_fn, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(content="ok")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        handle_chat("test", [], [])

        client.chat.completions.create.assert_called_once()

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_history_is_included_in_messages(self, mock_openai_fn, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(content="ok")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        history = [{"role": "user", "content": "mensaje anterior"}]
        handle_chat("nuevo mensaje", history, [])

        call_kwargs = client.chat.completions.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["messages"]
        contents = [m["content"] for m in messages]
        assert "mensaje anterior" in contents
        assert "nuevo mensaje" in contents

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_empty_catalog_does_not_crash(self, mock_openai_fn, mock_load_catalog):
        mock_load_catalog.return_value = []
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(content="ok")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        result = handle_chat("hola", [], [])
        assert "reply" in result

    @patch("backend.chat_agent_agentic._load_catalog")
    @patch("backend.chat_agent_agentic._openai")
    def test_result_always_has_required_keys(self, mock_openai_fn, mock_load_catalog, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        client = MagicMock()
        client.chat.completions.create.return_value = _make_openai_response(content="ok")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = client
        mock_openai_fn.return_value = mock_openai

        result = handle_chat("hola", [], [])
        assert "reply" in result
        assert "cart" in result
        assert "clarification" in result
