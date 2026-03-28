# Agentic Loop + search_products Tool (LangGraph) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-turn chat handler with a LangGraph-powered agentic graph where the LLM drives tool use — searching the catalog before adding products, threading tool results back into context, and only returning when it has a natural reply.

**Architecture:** A LangGraph `StateGraph` with two nodes: `agent` (calls the LLM with bound tools) and `tools` (executes tool calls locally). The graph loops `agent → tools → agent` until the LLM emits no more tool calls (`finish_reason == "stop"`), then exits via a conditional edge. State carries messages (OpenAI format), the resolved cart, and any pending clarification. `handle_chat()` remains the public entry point with the same signature and return shape — the server and tests see no interface change.

**Tech Stack:** Python 3, `langgraph`, `langchain-openai`, `langchain-core`, `backend/product_semantic_index.py::search()`, `pytest` + `unittest.mock`

---

## File Map

| File | Change |
|---|---|
| `requirements.txt` | Add `langgraph`, `langchain-openai`, `langchain-core` |
| `backend/chat_agent_agentic.py` | Full rewrite: LangGraph graph, tool node, agent node, same public `handle_chat()` |
| `tests/test_chat_agent.py` | Update system-prompt tests (no catalog param), add graph integration tests |

---

### Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add LangGraph packages**

Replace the contents of `requirements.txt` with:

```
openai>=1.0.0
langchain-openai>=0.2.0
langchain-core>=0.3.0
langgraph>=0.2.0
sentence-transformers>=3.0.0
pytest>=7.0.0
```

- [ ] **Step 2: Install**

```bash
cd /home/jerefigo/Documents/fun/hackITBA2026
pip install -r requirements.txt 2>&1 | tail -10
```
Expected: `Successfully installed` lines, no errors.

- [ ] **Step 3: Smoke-check imports**

```bash
python -c "import langgraph; import langchain_openai; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add langgraph, langchain-openai, langchain-core dependencies"
```

---

### Task 2: Update the broken system-prompt test

The current `test_prompt_contains_catalog_summary` passes a string into `_build_system_prompt`. The new prompt takes no arguments. Fix tests first so the suite stays green throughout.

**Files:**
- Modify: `tests/test_chat_agent.py`

- [ ] **Step 1: Verify current baseline**

```bash
pytest tests/test_chat_agent.py -v 2>&1 | tail -20
```
Expected: all pass.

- [ ] **Step 2: Update `TestSystemPrompt` in `tests/test_chat_agent.py`**

Replace the entire `TestSystemPrompt` class with:

```python
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
```

- [ ] **Step 3: Run — should FAIL (implementation unchanged)**

```bash
pytest tests/test_chat_agent.py::TestSystemPrompt -v 2>&1 | tail -10
```
Expected: `TypeError: _build_system_prompt() takes 1 positional argument but 0 were given`.

- [ ] **Step 4: Commit the test-only change**

```bash
git add tests/test_chat_agent.py
git commit -m "test(agent): update system prompt tests for no-catalog-dump signature"
```

---

### Task 3: Rewrite `chat_agent_agentic.py` as a LangGraph graph

This is the core task. The public interface (`handle_chat` signature and return shape) stays identical. Internally everything moves to a LangGraph `StateGraph`.

**Files:**
- Modify: `backend/chat_agent_agentic.py`

#### Key concepts before you write code

**LangGraph state** is a `TypedDict` that flows through the graph. Each node receives it and returns a partial update.

**`MessagesState`** is LangGraph's built-in state with a single `messages` key whose reducer appends (not replaces) incoming message lists.

**Nodes** are plain Python functions `(state) -> partial_state_update`.

**Conditional edges** inspect state to decide the next node. Return the node name as a string.

**`graph.compile()`** returns a runnable. Call `.invoke(initial_state)` to run it.

- [ ] **Step 1: Write the full replacement for `backend/chat_agent_agentic.py`**

```python
"""
Agentic shopping flow — LangGraph implementation.
Owner: Jeremias

Entry point: handle_chat()
Graph:  START → agent ⟶ tools ⟶ agent ⟶ … ⟶ END
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Annotated

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


# ─── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    catalog: list[dict]          # passed through, never mutated by nodes
    result_cart: list[dict] | None
    clarification: dict | None


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return """Eres un asistente de compras inteligente para supershop, un supermercado online.
Tu trabajo es ayudar al usuario a armar su carrito de compras de forma eficiente.

Flujo obligatorio para agregar productos:
1. Llamá a search_products con una query descriptiva para encontrar candidatos reales del catálogo.
2. Elegí el producto más adecuado de los resultados (mejor match de nombre, marca, tamaño).
3. Llamá a set_cart con el carrito COMPLETO actualizado (incluí los ítems previos más los nuevos).
4. Respondé al usuario nombrando el producto que agregaste y el precio.

Reglas:
- Respondé siempre en español, de forma amable y concisa.
- Nunca uses un product_id que no apareció en los resultados de search_products.
- Si no encontrás un producto exacto, usá el más cercano y explicalo.
- Si el usuario pregunta algo no relacionado con compras, redirigilo amablemente.
- Al actualizar el carrito, siempre incluí TODOS los ítems previos más los nuevos.
"""


# ─── Catalog helpers ──────────────────────────────────────────────────────────

def _load_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH) as f:
        return json.load(f)


def _catalog_summary(catalog: list[dict], max_items: int = 50) -> str:
    """Compact catalog representation (kept for tests and future use)."""
    lines = []
    for p in catalog[:max_items]:
        line = f"- [{p['id']}] {p['name']} ({p.get('brand','')}, {p.get('package_size','')}) ${p.get('price',0):.2f}"
        if p.get('discount_pct', 0) > 0:
            line += f" [{p['discount_pct']}% OFF]"
        lines.append(line)
    if len(catalog) > max_items:
        lines.append(f"... y {len(catalog) - max_items} productos más.")
    return "\n".join(lines)


def _validate_cart(items: list[dict], catalog: list[dict]) -> list[dict]:
    """Validate cart items against the catalog. Remove unknown or out-of-stock items."""
    catalog_by_id = {p["id"]: p for p in catalog}
    valid = []
    for item in items:
        pid = item.get("product_id")
        if pid and pid in catalog_by_id:
            p = catalog_by_id[pid]
            if p.get("available_quantity", 1) > 0:
                valid.append({
                    "product_id": pid,
                    "name": p["name"],
                    "brand": p.get("brand", ""),
                    "package_size": p.get("package_size", ""),
                    "price": p["price"],
                    "quantity": max(1, int(item.get("quantity", 1))),
                    "image_url": p.get("image_url", ""),
                })
    return valid


# ─── Tool implementations ─────────────────────────────────────────────────────
# These are plain functions — the LangGraph tool node calls them.
# They receive the catalog via a closure built inside build_graph().

def _make_tools(catalog: list[dict]):
    """Return LangChain tool objects closed over the catalog."""

    @tool
    def search_products(query: str, top_k: int = 5) -> str:
        """Search the product catalog for items matching a natural language query.
        Always call this before set_cart to find the correct product_id and price.
        Returns a ranked list of in-stock products as JSON."""
        from backend.product_semantic_index import search as _search
        results = _search(query, catalog, top_k=top_k)
        return json.dumps(results)

    @tool
    def set_cart(items: list[dict]) -> str:
        """Replace the entire cart with the given items.
        Each item must have product_id (from search_products results) and quantity.
        Call this after every cart mutation. Returns the validated cart as JSON."""
        validated = _validate_cart(items, catalog)
        return json.dumps({"cart": validated, "count": len(validated)})

    @tool
    def request_clarification(question: str, options: list[dict]) -> str:
        """Ask the user to choose between ambiguous product options before proceeding.
        Returns acknowledgement."""
        return json.dumps({"acknowledged": True})

    return [search_products, set_cart, request_clarification]


# ─── Graph ────────────────────────────────────────────────────────────────────

def _build_graph(catalog: list[dict]):
    """Compile and return a LangGraph graph closed over the catalog."""
    tools = _make_tools(catalog)
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=os.environ["OPENAI_API_KEY"],
    ).bind_tools(tools)

    tools_by_name = {t.name: t for t in tools}

    def agent_node(state: AgentState) -> dict:
        response = llm.invoke(state["messages"])
        return {"messages": [response]}

    def tools_node(state: AgentState) -> dict:
        last_msg = state["messages"][-1]
        result_cart = state.get("result_cart")
        clarification = state.get("clarification")
        new_messages = []

        for tc in last_msg.tool_calls:
            tool_fn = tools_by_name[tc["name"]]
            result = tool_fn.invoke(tc["args"])
            new_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

            # Capture side effects
            if tc["name"] == "set_cart":
                parsed = json.loads(result)
                result_cart = parsed.get("cart")
            elif tc["name"] == "request_clarification":
                args = tc["args"]
                clarification = {
                    "question": args.get("question", "¿Cuál preferís?"),
                    "options": args.get("options", []),
                    "pending_request_id": str(uuid.uuid4()),
                }

        return {
            "messages": new_messages,
            "result_cart": result_cart,
            "clarification": clarification,
        }

    def should_continue(state: AgentState) -> str:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile()


# ─── Public entry point ───────────────────────────────────────────────────────

def handle_chat(
    message: str,
    history: list[dict],
    cart: list[dict],
    clarification_response: dict | None = None,
) -> dict[str, Any]:
    """
    Process a chat turn and return:
        { "reply": str, "cart": list | None, "clarification": dict | None }

    Internally runs a LangGraph agent loop:
        START → agent → tools → agent → … → END
    """
    catalog = _load_catalog()
    app = _build_graph(catalog)

    # Build initial message list
    init_messages: list = [SystemMessage(content=_build_system_prompt())]

    if cart:
        cart_text = "Carrito actual:\n" + "\n".join(
            f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
            for i in cart
        )
        init_messages.append(SystemMessage(content=cart_text))

    if clarification_response:
        init_messages.append(SystemMessage(
            content=(
                f"El usuario eligió la opción: {clarification_response.get('chosen_option_id')} "
                f"para la solicitud pendiente: {clarification_response.get('pending_request_id')}"
            )
        ))

    for msg in history[-20:]:
        if msg["role"] == "user":
            init_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            init_messages.append(AIMessage(content=msg["content"]))

    init_messages.append(HumanMessage(content=message))

    final_state = app.invoke({
        "messages": init_messages,
        "catalog": catalog,
        "result_cart": None,
        "clarification": None,
    })

    last_msg = final_state["messages"][-1]
    reply = last_msg.content if isinstance(last_msg, AIMessage) else ""

    return {
        "reply": reply,
        "cart": final_state.get("result_cart"),
        "clarification": final_state.get("clarification"),
    }
```

- [ ] **Step 2: Verify no import errors**

```bash
cd /home/jerefigo/Documents/fun/hackITBA2026
python -c "from backend.chat_agent_agentic import handle_chat, _build_system_prompt, _validate_cart, _catalog_summary; print('imports ok')"
```
Expected: `imports ok`

- [ ] **Step 3: Run `TestSystemPrompt` — should now pass**

```bash
pytest tests/test_chat_agent.py::TestSystemPrompt -v 2>&1 | tail -10
```
Expected: all 5 pass.

- [ ] **Step 4: Run `TestCatalogSummary` and `TestValidateCart` — should still pass**

```bash
pytest tests/test_chat_agent.py::TestCatalogSummary tests/test_chat_agent.py::TestValidateCart -v 2>&1 | tail -15
```
Expected: all pass (both functions are unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/chat_agent_agentic.py
git commit -m "feat(agent): rewrite chat_agent_agentic as LangGraph StateGraph with search_products tool"
```

---

### Task 4: Update `TestHandleChat` for LangGraph internals

The existing `TestHandleChat` mocks `_openai()` which no longer exists. We need to mock `ChatOpenAI` instead.

**Files:**
- Modify: `tests/test_chat_agent.py`

- [ ] **Step 1: Replace `TestHandleChat` with the LangGraph-aware version**

Replace the entire `TestHandleChat` class with:

```python
class TestHandleChat:
    """Integration tests for handle_chat. Mocks the LLM at the ChatOpenAI level."""

    def _make_ai_message(self, content="ok", tool_calls=None):
        msg = MagicMock(spec=AIMessage)
        msg.content = content
        msg.tool_calls = tool_calls or []
        return msg

    def _make_tool_call(self, name, args, call_id="call_1"):
        return {"name": name, "args": args, "id": call_id}

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_returns_reply_on_plain_response(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.return_value = self._make_ai_message("Hola, ¿en qué te puedo ayudar?")
        mock_llm_cls.return_value = llm_instance

        result = handle_chat("Hola", [], [])

        assert result["reply"] == "Hola, ¿en qué te puedo ayudar?"
        assert result["cart"] is None
        assert result["clarification"] is None

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_result_always_has_required_keys(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.return_value = self._make_ai_message("ok")
        mock_llm_cls.return_value = llm_instance

        result = handle_chat("hola", [], [])

        assert "reply" in result
        assert "cart" in result
        assert "clarification" in result

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_empty_catalog_does_not_crash(self, mock_load_catalog, mock_llm_cls):
        mock_load_catalog.return_value = []
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.return_value = self._make_ai_message("ok")
        mock_llm_cls.return_value = llm_instance

        result = handle_chat("hola", [], [])

        assert "reply" in result

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_history_is_included_in_messages(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.return_value = self._make_ai_message("ok")
        mock_llm_cls.return_value = llm_instance

        history = [{"role": "user", "content": "mensaje anterior"}]
        handle_chat("nuevo mensaje", history, [])

        first_call_messages = llm_instance.invoke.call_args_list[0][0][0]
        contents = [m.content for m in first_call_messages]
        assert any("mensaje anterior" in c for c in contents)
        assert any("nuevo mensaje" in c for c in contents)
```

- [ ] **Step 2: Run `TestHandleChat` — should pass**

```bash
pytest tests/test_chat_agent.py::TestHandleChat -v 2>&1 | tail -15
```
Expected: all 4 pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_chat_agent.py
git commit -m "test(agent): update TestHandleChat to mock ChatOpenAI instead of raw openai"
```

---

### Task 5: Add agentic loop integration tests

**Files:**
- Modify: `tests/test_chat_agent.py`

- [ ] **Step 1: Add `TestAgenticLoop` class to `tests/test_chat_agent.py`**

Add this import at the top of the file alongside the others:

```python
from langchain_core.messages import AIMessage as LCAIMessage
```

Add this class at the end of the file:

```python
class TestAgenticLoop:
    """Tests that verify the graph loops correctly when tools are called."""

    def _make_tool_ai_msg(self, tool_name, tool_args, call_id="call_1"):
        msg = MagicMock(spec=LCAIMessage)
        msg.content = None
        msg.tool_calls = [{"name": tool_name, "args": tool_args, "id": call_id}]
        return msg

    def _make_reply_ai_msg(self, text):
        msg = MagicMock(spec=LCAIMessage)
        msg.content = text
        msg.tool_calls = []
        return msg

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_search_then_set_cart_then_reply(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """Full happy path: search → set_cart → final reply."""
        mock_load_catalog.return_value = sample_catalog
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.side_effect = [
            self._make_tool_ai_msg("search_products", {"query": "leche entera 1L"}),
            self._make_tool_ai_msg("set_cart", {"items": [{"product_id": "p1", "quantity": 1}]}, call_id="call_2"),
            self._make_reply_ai_msg("Agregué Leche entera La Serenísima 1L a tu carrito. $350.00"),
        ]
        mock_llm_cls.return_value = llm_instance

        result = handle_chat("quiero leche entera 1L", [], [])

        assert llm_instance.invoke.call_count == 3
        assert result["cart"] is not None
        assert result["cart"][0]["product_id"] == "p1"
        assert "leche" in result["reply"].lower()

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_set_cart_rejects_out_of_stock(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        """p3 is out of stock — set_cart returns empty cart."""
        mock_load_catalog.return_value = sample_catalog
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.side_effect = [
            self._make_tool_ai_msg("set_cart", {"items": [{"product_id": "p3", "quantity": 1}]}),
            self._make_reply_ai_msg("Lo siento, ese producto no está disponible."),
        ]
        mock_llm_cls.return_value = llm_instance

        result = handle_chat("quiero pan lactal", [], [])

        assert result["cart"] == [] or result["cart"] is None or (
            result["cart"] is not None and all(i["product_id"] != "p3" for i in result["cart"])
        )

    @patch("backend.chat_agent_agentic.ChatOpenAI")
    @patch("backend.chat_agent_agentic._load_catalog")
    def test_plain_reply_completes_in_one_llm_call(self, mock_load_catalog, mock_llm_cls, sample_catalog):
        mock_load_catalog.return_value = sample_catalog
        llm_instance = MagicMock()
        llm_instance.bind_tools.return_value = llm_instance
        llm_instance.invoke.return_value = self._make_reply_ai_msg("Hola, ¿en qué te puedo ayudar?")
        mock_llm_cls.return_value = llm_instance

        result = handle_chat("hola", [], [])

        assert llm_instance.invoke.call_count == 1
        assert result["reply"] == "Hola, ¿en qué te puedo ayudar?"
```

- [ ] **Step 2: Run `TestAgenticLoop` — should pass**

```bash
pytest tests/test_chat_agent.py::TestAgenticLoop -v 2>&1 | tail -15
```
Expected: all 3 pass.

- [ ] **Step 3: Run full test suite**

```bash
pytest -v 2>&1 | tail -40
```
Expected: all pass (pre-commit gate).

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_agent.py
git commit -m "test(agent): add TestAgenticLoop for LangGraph search→set_cart→reply flow"
```

---

### Task 6: Log the decision

- [ ] **Step 1: Append to `docs/LOG.md`**

```markdown
## 2026-03-27 — Jeremias — Switched agent to LangGraph

**Type:** architecture

Replaced the raw OpenAI agentic loop in `chat_agent_agentic.py` with a LangGraph `StateGraph`. Graph: `START → agent → tools → agent → … → END`. Added `search_products` tool (calls `product_semantic_index.search()`), updated `set_cart` and `request_clarification` as LangChain `@tool` functions. Added `langgraph`, `langchain-openai`, `langchain-core` to `requirements.txt`. CLAUDE.md non-negotiable updated to reflect LangGraph as the agent framework. Public `handle_chat()` signature and return shape unchanged.
```

- [ ] **Step 2: Commit**

```bash
git add docs/LOG.md
git commit -m "docs: log LangGraph architecture decision"
```

---

## Self-Review Checklist

### Spec coverage

| Requirement | Task |
|---|---|
| LangGraph graph with agent + tools nodes | Task 3 |
| `search_products` tool wired to `product_semantic_index.search()` | Task 3 |
| LLM drives the loop via conditional edge | Task 3 (`should_continue`) |
| Tool results injected as `ToolMessage` | Task 3 (`tools_node`) |
| Cart validated server-side | Task 3 (`_validate_cart` inside `set_cart` tool) |
| `handle_chat` public signature unchanged | Task 3 |
| API contract unchanged | No changes to `app.py` |
| System prompt updated (no catalog dump, search-first) | Task 3 |
| Tests updated for ChatOpenAI mock | Task 4 |
| Agentic loop tests | Task 5 |
| Dependencies added | Task 1 |
| Decision logged | Task 6 |

### Placeholder scan

No TBD, TODO, or vague steps. All code blocks are complete.

### Type consistency

- `_build_system_prompt() -> str` — zero arguments. Used in Task 2 tests and Task 3 implementation.
- `_validate_cart(items, catalog) -> list[dict]` — unchanged signature, used inside `set_cart` tool in Task 3.
- `handle_chat(message, history, cart, clarification_response) -> dict` — unchanged. Tests in Task 4 and 5 assert on `reply`, `cart`, `clarification`.
- `AgentState` fields: `messages`, `catalog`, `result_cart`, `clarification` — used consistently in `agent_node`, `tools_node`, `should_continue`, and `handle_chat`.
- LangGraph message types: `AIMessage`, `HumanMessage`, `SystemMessage`, `ToolMessage` — all imported from `langchain_core.messages` in Task 3; `LCAIMessage` alias used in Task 5 tests.
