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
    missing_items: list[str]     # ingredients/products the agent couldn't find


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return """Eres un asistente de compras inteligente para supershop, un supermercado online.
Tu trabajo es ayudar al usuario a armar su carrito de compras de forma eficiente.

## Flujo para un producto específico
1. Llamá a search_products con una query descriptiva.
2. Elegí el producto más adecuado (mejor match de nombre, marca, tamaño).
3. Llamá a set_cart con el carrito COMPLETO actualizado.
4. Respondé nombrando el producto y el precio.

## Flujo para recetas, menús o metas de compra (NUEVO en V2)
Cuando el usuario pida una receta, un menú semanal, o una meta amplia (ej: "quiero hacer una torta", "armame desayunos para la semana"):
1. Identificá TODOS los ingredientes o productos necesarios.
2. Para CADA ingrediente/producto, llamá a search_products por separado.
3. Por cada búsqueda:
   a. Si encontrás un match adecuado → incluilo en el carrito.
   b. Si encontrás algo parecido pero no exacto → usalo como sustituto y anotá la diferencia.
   c. Si no encontrás nada útil → llamá a report_missing con el nombre del ingrediente.
4. Al final, llamá a set_cart UNA SOLA VEZ con todos los ítems juntos (previos + nuevos).
5. Respondé con un resumen estructurado (ver formato abajo).

## Formato de respuesta para multi-producto
Terminá siempre con un resumen en tres secciones (omití las secciones vacías):
✅ Agregados: listá producto, marca y precio de cada ítem nuevo.
🔄 Sustituciones: explicá qué pediste y qué agregaste en su lugar y por qué.
❌ No disponibles: listá los ingredientes que no encontraste en el catálogo.

## Reglas generales
- Respondé siempre en español, de forma amable y concisa.
- NUNCA afirmes que un producto no está en el catálogo sin haber llamado primero a search_products. Tu conocimiento interno no refleja el catálogo real.
- Nunca uses un product_id que no apareció en los resultados de search_products.
- Al actualizar el carrito, siempre incluí TODOS los ítems previos más los nuevos.
- Si el usuario pregunta algo no relacionado con compras, redirigilo amablemente.
- NO uses markdown en tus respuestas: sin **, sin *, sin #, sin ![...](...), sin listas con guiones. Solo texto plano.
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

    @tool
    def report_missing(ingredient: str) -> str:
        """Record an ingredient or product that could not be found in the catalog.
        Call this when search_products returns no useful results for a needed item.
        ingredient: the name of the item that is unavailable."""
        return json.dumps({"recorded": True, "ingredient": ingredient})

    return [search_products, set_cart, request_clarification, report_missing]


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
        missing_items = list(state.get("missing_items") or [])
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
            elif tc["name"] == "report_missing":
                ingredient = tc["args"].get("ingredient", "")
                if ingredient and ingredient not in missing_items:
                    missing_items.append(ingredient)

        return {
            "messages": new_messages,
            "result_cart": result_cart,
            "clarification": clarification,
            "missing_items": missing_items,
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
    context: str | None = None,
) -> dict[str, Any]:
    """
    Process a chat turn and return:
        { "reply": str, "cart": list | None, "clarification": dict | None, "missing_items": list[str] }

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

    if context:
        init_messages.append(SystemMessage(content=context))

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
        "missing_items": [],
    })

    last_msg = final_state["messages"][-1]
    reply = last_msg.content if isinstance(last_msg, AIMessage) and last_msg.content else "Lo siento, no pude completar la acción."

    return {
        "reply": reply,
        "cart": final_state.get("result_cart"),
        "clarification": final_state.get("clarification"),
        "missing_items": final_state.get("missing_items") or [],
    }
