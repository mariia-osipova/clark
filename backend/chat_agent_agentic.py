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

def _build_graph(catalog: list[dict], api_key: str):
    """Compile and return a LangGraph graph closed over the catalog."""
    tools = _make_tools(catalog)
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=api_key,
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
            try:
                result = tool_fn.invoke(tc["args"])
            except Exception as exc:
                result = json.dumps({"error": str(exc)})
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
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    catalog = _load_catalog()
    app = _build_graph(catalog, api_key)

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
    reply = last_msg.content if isinstance(last_msg, AIMessage) and last_msg.content else "Lo siento, no pude completar la acción."

    return {
        "reply": reply,
        "cart": final_state.get("result_cart"),
        "clarification": final_state.get("clarification"),
    }
