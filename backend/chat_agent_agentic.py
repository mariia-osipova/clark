"""
Agentic shopping flow — LangGraph implementation.
Owner: Jeremias

Entry point: handle_chat()
Graph:  START → agent ⟶ tools ⟶ agent ⟶ … ⟶ END
"""

from __future__ import annotations

import json
import os
import sqlite3
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


# ─── Catalog cache ────────────────────────────────────────────────────────────

_catalog_cache: tuple[list[dict], float] | None = None  # (catalog, mtime)
_MAX_HISTORY_CHARS = 40_000


def _get_catalog() -> list[dict]:
    """Return catalog, reloading if the file changed."""
    global _catalog_cache
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except FileNotFoundError:
        return []
    if _catalog_cache is None or _catalog_cache[1] != mtime:
        _catalog_cache = (_load_catalog(), mtime)
    return _catalog_cache[0]


def _get_or_build_app(api_key: str, session_id: str = ""):
    """Build and return the compiled graph for this request."""
    catalog = _get_catalog()
    app = _build_graph(catalog, api_key, session_id=session_id)
    return app, catalog


def _reset_app_cache() -> None:
    """Invalidate the module-level catalog cache."""
    global _catalog_cache
    _catalog_cache = None


# ─── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    result_cart: list[dict] | None
    clarification: dict | None
    missing_items: list[str]     # ingredients/products the agent couldn't find


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return """Eres un asistente de compras para supershop, un supermercado online.

## Reglas de routing
1. Para cada producto pedido, llamá resolve_product(query, quantity).
2. Si resolve_product devuelve status "resolved", llamá add_to_cart(product_id, quantity) usando solo los datos del resultado.
3. Si devuelve status "needs_clarification", llamá request_clarification con la pregunta y las options devueltas.
4. Si devuelve status "not_found", llamá report_missing con el nombre del producto.

## Reglas generales
- Nunca inventes product_ids ni productos fuera del catálogo.
- Para pedidos con varios productos, resolvé cada ítem por separado.
- Al terminar, contá brevemente qué agregaste, qué faltó y qué necesita aclaración.
- Respondé siempre en español, de forma amable y concisa.
- Sin markdown: sin **, sin *, sin #, sin guiones de lista.
- Si el usuario pregunta algo no relacionado con compras, redirigilo amablemente.
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


def _build_cart_item(product: dict, quantity: Any) -> dict:
    return {
        "product_id": product["id"],
        "name": product["name"],
        "brand": product.get("brand", ""),
        "package_size": product.get("package_size", ""),
        "price": product["price"],
        "quantity": max(1, int(quantity)),
        "image_url": product.get("image_url", ""),
    }


def _validate_cart(items: list[dict], catalog: list[dict]) -> list[dict]:
    """Validate cart items against the catalog. Remove unknown or out-of-stock items."""
    catalog_by_id = {p["id"]: p for p in catalog}
    valid: list[dict] = []
    for item in items:
        pid = item.get("product_id")
        if pid and pid in catalog_by_id:
            p = catalog_by_id[pid]
            if p.get("available_quantity", 1) > 0:
                valid.append(_build_cart_item(p, item.get("quantity", 1)))
    return valid


def _validate_cart_with_report(
    items: list[dict], catalog: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Return the validated cart alongside dropped items and the reason."""
    catalog_by_id = {p["id"]: p for p in catalog}
    valid: list[dict] = []
    dropped: list[dict] = []

    for item in items:
        pid = item.get("product_id")
        product = catalog_by_id.get(pid)
        if not pid or not product:
            dropped.append({"product_id": pid or "(missing)", "reason": "unknown product"})
            continue
        if product.get("available_quantity", 1) <= 0:
            dropped.append({"product_id": pid, "reason": "out of stock"})
            continue
        valid.append(_build_cart_item(product, item.get("quantity", 1)))

    return valid, dropped


def _upsert_local_cart_item(
    items: list[dict] | None,
    product_id: str,
    quantity: Any,
    catalog: list[dict],
) -> list[dict]:
    """Return a new in-memory cart with the item inserted or updated."""
    catalog_by_id = {p["id"]: p for p in catalog}
    product = catalog_by_id.get(product_id)
    if not product or product.get("available_quantity", 1) <= 0:
        return list(items or [])

    next_items = [item for item in (items or []) if item.get("product_id") != product_id]
    next_items.append(_build_cart_item(product, quantity))
    return next_items


def _merge_local_carts(
    base_items: list[dict] | None,
    added_items: list[dict] | None,
    catalog: list[dict],
) -> list[dict]:
    """Merge new cart items into an existing in-memory cart."""
    merged = list(base_items or [])
    for item in added_items or []:
        product_id = item.get("product_id", "")
        if product_id:
            merged = _upsert_local_cart_item(
                merged,
                product_id,
                item.get("quantity", 1),
                catalog,
            )
    return merged


def _trim_history(history: list[dict]) -> list[dict]:
    """Return the newest history entries that fit within the character budget."""
    trimmed: list[dict] = []
    chars = 0
    for message in reversed(history):
        content_len = len(str(message.get("content", "")))
        if chars + content_len > _MAX_HISTORY_CHARS:
            break
        trimmed.insert(0, message)
        chars += content_len
    return trimmed


# ─── Cart DB helper ───────────────────────────────────────────────────────────

def _write_session_cart_item(session_id: str, product_id: str, quantity: int) -> None:
    """Persist one cart item, creating the table on first use if needed."""
    from backend.db import get_db, init_db

    statement = """INSERT INTO session_carts (session_id, product_id, quantity)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id, product_id) DO UPDATE SET quantity = excluded.quantity"""

    for attempt in range(2):
        conn = get_db()
        try:
            conn.execute(statement, (session_id, product_id, quantity))
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if attempt == 0 and "no such table: session_carts" in str(exc):
                init_db()
                continue
            raise
        finally:
            conn.close()

def _read_session_cart(session_id: str, catalog: list[dict]) -> list[dict]:
    """Read session cart from DB and hydrate with current catalog data."""
    if not session_id:
        return []
    from backend.db import get_db, init_db

    rows = []
    for attempt in range(2):
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT product_id, quantity FROM session_carts WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            break
        except sqlite3.OperationalError as exc:
            if attempt == 0 and "no such table: session_carts" in str(exc):
                init_db()
                continue
            raise
        finally:
            conn.close()

    catalog_by_id = {p["id"]: p for p in catalog}
    result = []
    for row in rows:
        p = catalog_by_id.get(row["product_id"])
        if p and p.get("available_quantity", 1) > 0:
            result.append(_build_cart_item(p, row["quantity"]))
    return result


# ─── Tool implementations ─────────────────────────────────────────────────────

def _make_tools(catalog: list[dict], session_id: str = ""):
    """Return LangChain tool objects closed over the catalog and session."""

    @tool
    def resolve_product(query: str, quantity: int = 1) -> str:
        """Resolve a single product from a natural-language query.
        Returns a verdict dict: {status, product, quantity} or {status, options, quantity}.
        Route based on status: resolved → add_to_cart; needs_clarification → request_clarification; not_found → report_missing."""
        from backend.product_semantic_index import resolve_product as _resolve
        verdict = _resolve(query, quantity, catalog)
        return json.dumps(verdict)

    @tool
    def add_to_cart(product_id: str, quantity: int = 1) -> str:
        """Add or update one item in the session cart.
        product_id must come from a resolve_product verdict with status=resolved.
        quantity must be a positive integer."""
        catalog_by_id = {p["id"]: p for p in catalog}
        p = catalog_by_id.get(product_id)
        if not p:
            return json.dumps({"added": False, "error": "product not found"})
        if p.get("available_quantity", 1) <= 0:
            return json.dumps({"added": False, "error": "out of stock"})
        qty = max(1, int(quantity))
        if session_id:
            _write_session_cart_item(session_id, product_id, qty)
        return json.dumps({
            "added": True,
            "product_id": product_id,
            "name": p.get("name", ""),
            "quantity": qty,
            "price": p["price"],
        })

    @tool
    def request_clarification(question: str, options: list[dict]) -> str:
        """Ask the user to choose between ambiguous product options before proceeding.
        Returns acknowledgement and the pending request id."""
        pending_id = str(uuid.uuid4())
        return json.dumps({
            "acknowledged": True,
            "pending_request_id": pending_id,
            "question": question,
            "options": options,
        })

    @tool
    def report_missing(ingredient: str) -> str:
        """Record an ingredient or product that could not be found in the catalog.
        ingredient: the name of the item that is unavailable."""
        return json.dumps({"recorded": True, "ingredient": ingredient})

    return [resolve_product, add_to_cart, request_clarification, report_missing]


# ─── Graph ────────────────────────────────────────────────────────────────────

def _build_graph(catalog: list[dict], api_key: str, session_id: str = ""):
    """Compile and return a LangGraph graph closed over the catalog."""
    tools = _make_tools(catalog, session_id=session_id)
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=api_key,
        timeout=30,
    ).bind_tools(tools)

    tools_by_name = {t.name: t for t in tools}

    def agent_node(state: AgentState) -> dict:
        response = llm.invoke(state["messages"])
        return {"messages": [response]}

    def tools_node(state: AgentState) -> dict:
        last_msg = state["messages"][-1]
        clarification = state.get("clarification")
        missing_items = list(state.get("missing_items") or [])
        result_cart = list(state.get("result_cart") or [])
        new_messages = []

        for tc in last_msg.tool_calls:
            if clarification:
                break
            tool_fn = tools_by_name.get(tc["name"])
            if tool_fn is None:
                new_messages.append(
                    ToolMessage(
                        content=json.dumps({"error": f"unknown tool: {tc['name']}"}),
                        tool_call_id=tc["id"],
                    )
                )
                continue
            try:
                result = tool_fn.invoke(tc["args"])
            except Exception:
                result = json.dumps({"error": "tool execution failed"})
            new_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

            if tc["name"] == "request_clarification":
                parsed = json.loads(result)
                pending_message = ""
                for msg in reversed(state["messages"]):
                    if isinstance(msg, HumanMessage):
                        pending_message = msg.content
                        break
                clarification = {
                    "question": parsed.get("question", tc["args"].get("question", "¿Cuál preferís?")),
                    "options": parsed.get("options", tc["args"].get("options", [])),
                    "pending_request_id": parsed.get("pending_request_id"),
                    "pending_message": pending_message,
                }
            elif tc["name"] == "report_missing":
                ingredient = tc["args"].get("ingredient", "").strip().lower()
                if ingredient and ingredient not in missing_items:
                    missing_items.append(ingredient)
            elif tc["name"] == "add_to_cart" and not session_id:
                try:
                    parsed = json.loads(result)
                except json.JSONDecodeError:
                    parsed = {}
                if parsed.get("added"):
                    result_cart = _upsert_local_cart_item(
                        result_cart,
                        parsed.get("product_id", tc["args"].get("product_id", "")),
                        parsed.get("quantity", tc["args"].get("quantity", 1)),
                        catalog,
                    )

        return {
            "messages": new_messages,
            "result_cart": result_cart or None,
            "clarification": clarification,
            "missing_items": missing_items,
        }

    def should_continue(state: AgentState) -> str:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    def after_tools(state: AgentState) -> str:
        if state.get("clarification"):
            return END
        return "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_conditional_edges("tools", after_tools)

    return graph.compile()


# ─── Public entry point ───────────────────────────────────────────────────────

def _handle_generate_basket(catalog: list[dict]) -> dict[str, Any]:
    """
    Short-circuit handler for action='generate_monthly_basket'.
    Loads the recurring plan and recent orders from DB and returns a proposed cart.
    """
    from backend.db import get_db, init_db
    from backend.product_semantic_index import generate_monthly_basket_candidates

    try:
        init_db()
    except Exception:
        pass

    conn = get_db()
    try:
        plan_row = conn.execute(
            "SELECT * FROM recurring_plans WHERE id='default'"
        ).fetchone()
        order_rows = conn.execute(
            "SELECT cart_json, total, created_at FROM orders ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()

    if not plan_row:
        return {
            "reply": "No tenés un plan de compras mensual guardado. Configuralo en la pestaña de compras recurrentes.",
            "proposed_cart": [],
            "cart": None,
            "clarification": None,
            "missing_items": [],
        }

    plan = {
        "household_size": plan_row["household_size"],
        "monthly_budget": plan_row["monthly_budget"],
        "must_haves": json.loads(plan_row["priority_items"]),
        "preferred_brands": json.loads(plan_row["preferred_brands"]),
        "strict_brand": bool(plan_row["strict_brand"]),
        "excluded_categories": json.loads(plan_row["excluded_categories"]),
        "notes": plan_row["notes"],
    }

    order_history: list[list[dict]] = []
    for row in order_rows:
        try:
            items = json.loads(row["cart_json"])
        except Exception:
            items = []
        order_history.append(items if isinstance(items, list) else [])

    budget = plan.get("monthly_budget") or float("inf")
    candidates = generate_monthly_basket_candidates(
        prefs=plan,
        order_history=order_history,
        catalog=catalog,
        budget=budget,
    )

    catalog_by_id = {product["id"]: product for product in catalog}
    proposed_cart: list[dict] = []
    for candidate in candidates:
        product = candidate.get("product")
        if not product:
            continue
        product_id = product.get("id")
        catalog_product = catalog_by_id.get(product_id)
        if not product_id or not catalog_product:
            continue
        if catalog_product.get("available_quantity", 1) <= 0:
            continue
        proposed_item = _build_cart_item(catalog_product, candidate.get("quantity", 1))
        proposed_item["tag"] = candidate.get("tag", "suggested")
        proposed_cart.append(proposed_item)

    total = round(sum(item["price"] * item["quantity"] for item in proposed_cart), 2)
    must_haves = sum(1 for item in proposed_cart if item.get("tag") == "must_have")
    recurring = sum(1 for item in proposed_cart if item.get("tag") == "recurring")
    offers = sum(1 for item in proposed_cart if item.get("tag") == "offer")

    reply = (
        f"Generé tu canasta mensual con {len(proposed_cart)} productos por ${total:.2f}. "
        f"{must_haves} esenciales, {recurring} recurrentes, {offers} con descuento. "
        "Revisá el detalle y confirmá cuando quieras."
    )

    return {
        "reply": reply,
        "proposed_cart": proposed_cart,
        "cart": None,
        "clarification": None,
        "missing_items": [],
    }


def handle_chat(
    message: str,
    history: list[dict],
    cart: list[dict],
    clarification_response: dict | None = None,
    context: str | None = None,
    session_id: str = "",
    action: str | None = None,
) -> dict[str, Any]:
    """
    Process a chat turn and return:
        { "reply": str, "cart": list | None, "clarification": dict | None, "missing_items": list[str] }

    Internally runs a LangGraph agent loop:
        START → agent → tools → agent → … → END
    """
    if action == "generate_monthly_basket":
        return _handle_generate_basket(_get_catalog())

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    app, _catalog = _get_or_build_app(api_key, session_id=session_id)
    initial_cart = _validate_cart(cart, _catalog)

    # Build initial message list
    init_messages: list = [SystemMessage(content=_build_system_prompt())]

    if initial_cart:
        cart_text = "Carrito actual:\n" + "\n".join(
            f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
            for i in initial_cart
        )
        init_messages.append(SystemMessage(content=cart_text))

    if context:
        init_messages.append(SystemMessage(content=context))

    if clarification_response:
        chosen_id = clarification_response.get("chosen_option_id", "")
        product = next((p for p in _catalog if p.get("id") == chosen_id), None)
        if product:
            qty = 1
            if session_id:
                _write_session_cart_item(session_id, chosen_id, qty)
                validated_cart = _read_session_cart(session_id, _catalog)
            else:
                new_items = list(initial_cart) + [{"product_id": product["id"], "quantity": qty}]
                validated_cart, _ = _validate_cart_with_report(new_items, _catalog)

            pending_msg = clarification_response.get("pending_message", "").strip()

            if not pending_msg:
                product_label = " ".join(filter(None, [product.get("name", ""), product.get("package_size", "")]))
                return {
                    "reply": f"Listo, agregué {product_label} al carrito.",
                    "cart": validated_cart,
                    "clarification": None,
                    "missing_items": [],
                }

            # Multi-item: continue graph with resolved product in context
            product_label = " ".join(filter(None, [product.get("name", ""), product.get("package_size", "")]))
            cont_messages: list = [SystemMessage(content=_build_system_prompt())]
            if validated_cart:
                cart_lines = "\n".join(
                    f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
                    for i in validated_cart
                )
                cont_messages.append(SystemMessage(content=f"Carrito actual:\n{cart_lines}"))
            if context:
                cont_messages.append(SystemMessage(content=context))
            cont_messages.append(SystemMessage(content=(
                f"Ya resolviste la ambigüedad: el usuario eligió '{product_label}' "
                f"(product_id={product['id']}) y fue agregado al carrito. "
                "Continuá procesando el pedido original. "
                "No vuelvas a buscar ese producto."
            )))
            cont_messages.append(HumanMessage(content=pending_msg))

            cont_state = app.invoke(
                {
                    "messages": cont_messages,
                    "result_cart": None,
                    "clarification": None,
                    "missing_items": [],
                },
                config={"recursion_limit": 30},
            )

            last_msg = cont_state["messages"][-1]
            cont_clar = cont_state.get("clarification")
            if isinstance(last_msg, AIMessage) and last_msg.content:
                reply = last_msg.content
            elif cont_clar:
                reply = cont_clar.get("question", "Necesito una aclaración para continuar.")
            else:
                reply = f"Listo, agregué {product_label} al carrito."

            final_cart = (
                _read_session_cart(session_id, _catalog)
                if session_id
                else _merge_local_carts(validated_cart, cont_state.get("result_cart"), _catalog)
            )
            return {
                "reply": reply,
                "cart": final_cart,
                "clarification": cont_clar,
                "missing_items": cont_state.get("missing_items") or [],
            }

    for msg in _trim_history(history):
        if msg["role"] == "user":
            init_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            init_messages.append(AIMessage(content=msg["content"]))

    init_messages.append(HumanMessage(content=message))

    final_state = app.invoke(
        {
            "messages": init_messages,
            "result_cart": None if session_id else (initial_cart or None),
            "clarification": None,
            "missing_items": [],
        },
        config={"recursion_limit": 30},
    )

    last_msg = final_state["messages"][-1]
    clarification = final_state.get("clarification")
    if isinstance(last_msg, AIMessage) and last_msg.content:
        reply = last_msg.content
    elif clarification:
        reply = clarification.get("question", "Necesito una aclaración para continuar.")
    else:
        reply = "Lo siento, no pude completar la acción."

    cart_result = _read_session_cart(session_id, _catalog) if session_id else final_state.get("result_cart")

    return {
        "reply": reply,
        "cart": cart_result,
        "clarification": clarification,
        "missing_items": final_state.get("missing_items") or [],
    }
