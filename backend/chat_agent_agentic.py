"""
Agentic shopping flow — LangGraph implementation.
Owner: Jeremias

Entry point: handle_chat()
Graph:  START → agent ⟶ tools ⟶ agent ⟶ … ⟶ END

State persists across turns via SqliteSaver (same DB file as the rest of the app).
"""

from __future__ import annotations

import json
import logging
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
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


# ─── Module-level singletons ──────────────────────────────────────────────────

_catalog_cache: tuple[list[dict], float] | None = None
_app_cache: dict = {}          # keys: app, catalog_mtime, api_key
_checkpointer = None           # SqliteSaver, initialized lazily
_MAX_HISTORY_CHARS = 40_000


def _get_checkpointer():
    global _checkpointer
    if _checkpointer is None:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from backend.db import get_db_path, init_db
        try:
            init_db()
        except Exception:
            pass
        conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
        _checkpointer = SqliteSaver(conn)
        _checkpointer.setup()
    return _checkpointer


def _get_catalog() -> list[dict]:
    global _catalog_cache
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except FileNotFoundError:
        return []
    if _catalog_cache is None or _catalog_cache[1] != mtime:
        _catalog_cache = (_load_catalog(), mtime)
    return _catalog_cache[0]


def _get_or_build_app(api_key: str):
    """Return cached (app, catalog), rebuilding only when catalog or api_key changes."""
    global _app_cache
    catalog = _get_catalog()
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0

    if (
        not _app_cache.get("app")
        or _app_cache.get("catalog_mtime") != mtime
        or _app_cache.get("api_key") != api_key
    ):
        app = _build_graph(catalog, api_key)
        _app_cache = {"app": app, "catalog_mtime": mtime, "api_key": api_key}

    return _app_cache["app"], catalog


def _reset_app_cache() -> None:
    global _catalog_cache, _app_cache
    _catalog_cache = None
    _app_cache = {}


# ─── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    result_cart: list[dict] | None
    clarification: dict | None
    missing_items: list[str]
    resolved_queries: dict  # query_text → product dict, prevents re-triggering clarification


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return """Eres un asistente de compras para supershop, un supermercado online.

## REGLA FUNDAMENTAL
Cuando el usuario menciona cualquier producto o alimento, tu PRIMERA acción debe ser llamar a las herramientas — no respondas con texto primero. Nunca pidas confirmación antes de buscar. Nunca preguntes "¿Te gustaría que busque...?". Simplemente buscá.

## Flujo obligatorio para cada producto mencionado
1. Llamá resolve_product(query, quantity) de inmediato.
2. Si devuelve status "resolved" → llamá add_to_cart(product_id, quantity) con los datos del resultado.
3. Si devuelve status "needs_clarification" → llamá request_clarification con la pregunta y las options exactas devueltas.
4. Si devuelve status "not_found" → llamá report_missing con el nombre del producto.

## Reglas generales
- NUNCA respondas con texto sin antes haber llamado las herramientas para cada producto pedido.
- NUNCA preguntes "¿quieres que busque?", "¿te gustaría que...?", ni nada similar. Buscá directamente.
- Para pedidos con varios productos, resolvé cada ítem por separado con su propia llamada a resolve_product.
- Nunca inventes product_ids; usá únicamente los devueltos por resolve_product.
- Al terminar, respondé brevemente qué agregaste, qué faltó y qué necesita aclaración.
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
    merged = list(base_items or [])
    for item in added_items or []:
        product_id = item.get("product_id", "")
        if product_id:
            merged = _upsert_local_cart_item(merged, product_id, item.get("quantity", 1), catalog)
    return merged


def _trim_history(history: list[dict]) -> list[dict]:
    """Return the newest messages within budget, keeping turn boundaries intact.
    Groups consecutive user+assistant pairs; unpaired messages are their own unit."""
    units: list[list[dict]] = []
    i = 0
    while i < len(history):
        if (
            i + 1 < len(history)
            and history[i].get("role") == "user"
            and history[i + 1].get("role") == "assistant"
        ):
            units.append([history[i], history[i + 1]])
            i += 2
        else:
            units.append([history[i]])
            i += 1

    selected: list[dict] = []
    chars = 0
    for unit in reversed(units):
        unit_len = sum(len(str(m.get("content", ""))) for m in unit)
        if chars + unit_len > _MAX_HISTORY_CHARS:
            break
        selected = unit + selected
        chars += unit_len
    return selected


# ─── Cart DB helpers ──────────────────────────────────────────────────────────

def _write_session_cart_items(session_id: str, items: list[tuple[str, int]]) -> None:
    """Persist multiple cart items atomically in a single transaction."""
    if not session_id or not items:
        return
    from backend.db import get_db, init_db

    statement = """INSERT INTO session_carts (session_id, product_id, quantity)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id, product_id) DO UPDATE SET quantity = excluded.quantity"""

    for attempt in range(2):
        conn = get_db()
        try:
            conn.executemany(statement, [(session_id, pid, qty) for pid, qty in items])
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if attempt == 0 and "no such table: session_carts" in str(exc):
                init_db()
                continue
            raise
        finally:
            conn.close()


def _write_session_cart_item(session_id: str, product_id: str, quantity: int) -> None:
    _write_session_cart_items(session_id, [(product_id, quantity)])


def _read_session_cart(session_id: str, catalog: list[dict]) -> list[dict]:
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

def _make_tools(catalog: list[dict]):
    """Return LangChain tool objects closed over the catalog.
    Session-specific side effects (DB writes) are handled in tools_node, not here."""

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
        """Validate and stage one item for the cart.
        product_id must come from a resolve_product verdict with status=resolved.
        quantity must be a positive integer."""
        catalog_by_id = {p["id"]: p for p in catalog}
        p = catalog_by_id.get(product_id)
        if not p:
            return json.dumps({"added": False, "error": "product not found"})
        if p.get("available_quantity", 1) <= 0:
            return json.dumps({"added": False, "error": "out of stock"})
        qty = max(1, int(quantity))
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
        """Record an ingredient or product that could not be found in the catalog."""
        return json.dumps({"recorded": True, "ingredient": ingredient})

    return [resolve_product, add_to_cart, request_clarification, report_missing]


# ─── Reply extraction helper ──────────────────────────────────────────────────

def _extract_reply(state: dict, fallback_label: str | None = None) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    clarification = state.get("clarification")
    if clarification:
        return clarification.get("question", "Necesito una aclaración para continuar.")
    if fallback_label:
        return f"Listo, agregué {fallback_label} al carrito."
    return "Lo siento, no pude completar la acción."


# ─── Graph ────────────────────────────────────────────────────────────────────

def _build_graph(catalog: list[dict], api_key: str):
    """Compile and return a LangGraph graph with SqliteSaver checkpointer."""
    tools = _make_tools(catalog)
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=api_key,
        timeout=30,
    ).bind_tools(tools)

    tools_by_name = {t.name: t for t in tools}

    def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        # Keep all SystemMessages plus the last 30 non-system messages to avoid
        # blowing the context window on long clarification chains.
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]
        trimmed = system_msgs + non_system[-30:]
        response = llm.invoke(trimmed)
        return {"messages": [response]}

    def tools_node(state: AgentState, config: RunnableConfig) -> dict:
        session_id = (config.get("configurable") or {}).get("session_id", "")
        last_msg = state["messages"][-1]
        missing_items = list(state.get("missing_items") or [])
        result_cart = list(state.get("result_cart") or [])
        clarification = state.get("clarification")

        catalog_by_id = {p["id"]: p for p in catalog}
        pending_message = next(
            (str(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        resolved_queries: dict = dict(state.get("resolved_queries") or {})

        # Pass 1: execute ALL tool calls in original order.
        # For add_to_cart calls, if the product_id was not verified by a resolve_product
        # call earlier in the same batch, run a clarification pre-check first so the LLM
        # cannot bypass the ambiguity gate by calling add_to_cart directly.
        tool_results: list[tuple[dict, str]] = []
        resolved_this_batch: dict[str, dict] = {}  # pid → product dict
        last_resolve_query: str = ""  # last resolve_product query in this batch

        for tc in last_msg.tool_calls:
            tool_fn = tools_by_name.get(tc["name"])
            if tool_fn is None:
                tool_results.append((tc, json.dumps({"error": f"unknown tool: {tc['name']}"})))
                continue

            if tc["name"] == "add_to_cart":
                pid = str(tc["args"].get("product_id", ""))
                qty = tc["args"].get("quantity", 1)

                # If this pid was not resolved in the current batch, run a quick
                # clarification check to see whether the product is actually ambiguous.
                if pid and pid not in resolved_this_batch and not clarification:
                    product = catalog_by_id.get(pid)
                    if product:
                        try:
                            check_str = tools_by_name["resolve_product"].invoke(
                                {"query": product["name"], "quantity": qty}
                            )
                            check = json.loads(check_str)
                            if check.get("status") == "needs_clarification" and check.get("options"):
                                clarification = {
                                    "question": f"¿Cuál {product.get('name', 'producto')} preferís?",
                                    "options": check["options"],
                                    "pending_request_id": str(uuid.uuid4()),
                                    "pending_message": pending_message,
                                    "original_query": product.get("name", ""),
                                }
                                # Block this add_to_cart — tell the LLM why
                                tool_results.append((tc, json.dumps({
                                    "added": False,
                                    "error": "product requires clarification before adding",
                                })))
                                continue
                        except Exception:
                            _log.exception("clarification pre-check failed for pid %s", pid)

                try:
                    result = tool_fn.invoke(tc["args"])
                except Exception:
                    _log.exception("tool add_to_cart failed with args %s", tc["args"])
                    result = json.dumps({"error": "tool execution failed"})
                tool_results.append((tc, result))

            elif tc["name"] == "resolve_product":
                query = tc["args"].get("query", "")
                # If this query was already resolved by a prior clarification, return
                # the cached resolved result so the agent can continue without looping.
                # Use substring matching to handle variations like "mascarpone" vs "queso mascarpone".
                query_lower = query.lower().strip()
                cached = resolved_queries.get(query_lower)
                if not cached:
                    for stored_key, stored_val in resolved_queries.items():
                        if stored_key in query_lower or query_lower in stored_key:
                            cached = stored_val
                            break
                if cached:
                    synthetic = json.dumps({"status": "resolved", "product": cached})
                    tool_results.append((tc, synthetic))
                    resolved_this_batch[cached.get("id", "")] = cached
                    last_resolve_query = query_lower
                    continue

                last_resolve_query = query_lower
                try:
                    result = tool_fn.invoke(tc["args"])
                except Exception:
                    _log.exception("tool %s failed with args %s", tc["name"], tc["args"])
                    result = json.dumps({"error": "tool execution failed"})
                tool_results.append((tc, result))

                # Track product_ids that are now safely resolved in this batch
                try:
                    parsed = json.loads(result)
                    if parsed.get("status") == "resolved" and parsed.get("product", {}).get("id"):
                        resolved_this_batch[parsed["product"]["id"]] = parsed["product"]
                except json.JSONDecodeError:
                    pass

            else:
                try:
                    result = tool_fn.invoke(tc["args"])
                except Exception:
                    _log.exception("tool %s failed with args %s", tc["name"], tc["args"])
                    result = json.dumps({"error": "tool execution failed"})
                tool_results.append((tc, result))

        # Pass 2: build ToolMessages and process side effects
        new_messages: list = []
        db_writes: list[tuple[str, int]] = []

        for tc, result in tool_results:
            new_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

            if tc["name"] == "request_clarification" and not clarification:
                try:
                    parsed = json.loads(result)
                except json.JSONDecodeError:
                    parsed = {}
                clarification = {
                    "question": parsed.get("question", tc["args"].get("question", "¿Cuál preferís?")),
                    "options": parsed.get("options", tc["args"].get("options", [])),
                    "pending_request_id": parsed.get("pending_request_id"),
                    "pending_message": pending_message,
                    "original_query": last_resolve_query,
                }

            elif tc["name"] == "report_missing":
                ingredient = tc["args"].get("ingredient", "").strip().lower()
                if ingredient and ingredient not in missing_items:
                    missing_items.append(ingredient)

            elif tc["name"] == "add_to_cart":
                try:
                    parsed = json.loads(result)
                except json.JSONDecodeError:
                    parsed = {}
                if parsed.get("added"):
                    pid = parsed.get("product_id", tc["args"].get("product_id", ""))
                    qty = parsed.get("quantity", tc["args"].get("quantity", 1))
                    if session_id:
                        db_writes.append((pid, qty))
                    else:
                        result_cart = _upsert_local_cart_item(result_cart, pid, qty, catalog)

        # Structural invariant: catch any resolve_product needs_clarification that the
        # agent acknowledged but didn't forward to request_clarification.
        if not clarification:
            for tc, result in tool_results:
                if tc["name"] == "resolve_product":
                    try:
                        parsed = json.loads(result)
                    except json.JSONDecodeError:
                        continue
                    if parsed.get("status") == "needs_clarification" and parsed.get("options"):
                        clarification = {
                            "question": f"¿Cuál {tc['args'].get('query', 'producto')} preferís?",
                            "options": parsed["options"],
                            "pending_request_id": str(uuid.uuid4()),
                            "pending_message": pending_message,
                            "original_query": tc["args"].get("query", ""),
                        }
                        break

        if session_id and db_writes:
            _write_session_cart_items(session_id, db_writes)

        return {
            "messages": new_messages,
            "result_cart": result_cart or None,
            "clarification": clarification,
            "missing_items": missing_items,
            "resolved_queries": resolved_queries,
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

    return graph.compile(checkpointer=_get_checkpointer())


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

    # order_history: list[list[dict]] — each order is a flat list of cart items
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

    Graph state persists across turns via SqliteSaver (thread_id = session_id).
    """
    if action == "generate_monthly_basket":
        return _handle_generate_basket(_get_catalog())

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")

    app, catalog = _get_or_build_app(api_key)
    initial_cart = _validate_cart(cart, catalog)

    # Use session_id as thread_id; fall back to a fresh UUID for stateless requests
    thread_id = session_id or str(uuid.uuid4())
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "session_id": session_id},
        "recursion_limit": 50,
    }

    # ── Clarification response ─────────────────────────────────────────────────
    if clarification_response:
        chosen_id = str(clarification_response.get("chosen_option_id", "") or "").strip()
        product = next((p for p in catalog if p.get("id") == chosen_id), None)
        if product:
            qty = 1
            product_label = " ".join(filter(None, [product.get("name", ""), product.get("package_size", "")]))

            # Retrieve the original query and pending message from the prior clarification state
            prior_state = None
            original_query = ""
            pending_message = ""
            if session_id:
                try:
                    prior_state = app.get_state(config)
                    prior_clarification = prior_state.values.get("clarification") or {}
                    original_query = (prior_clarification.get("original_query") or "").lower().strip()
                    pending_message = prior_clarification.get("pending_message") or ""
                except Exception:
                    pass

            if session_id:
                _write_session_cart_item(session_id, chosen_id, qty)

                # Cache the resolved query so tools_node won't trigger clarification again.
                # Then re-invoke the graph with the original user request so the agent
                # continues adding the remaining ingredients.
                if original_query and pending_message:
                    # Get existing resolved_queries and add this resolution
                    existing_resolved = dict((prior_state.values.get("resolved_queries") or {}) if prior_state else {})
                    existing_resolved[original_query] = product
                    app.update_state(
                        config,
                        {"clarification": None, "resolved_queries": existing_resolved},
                    )
                    # Re-invoke graph to continue with remaining ingredients
                    current_cart = _read_session_cart(session_id, catalog)
                    cart_lines = "\n".join(
                        f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
                        for i in current_cart
                    ) if current_cart else "vacío"
                    resume_input = {
                        "messages": [
                            SystemMessage(content=f"Carrito actual (actualizado):\n{cart_lines}"),
                            HumanMessage(content=pending_message),
                        ],
                        "clarification": None,
                        "missing_items": [],
                    }
                    final_state = app.invoke(resume_input, config=config)
                    next_clarification = final_state.get("clarification")
                    return {
                        "reply": _extract_reply(final_state),
                        "cart": _read_session_cart(session_id, catalog),
                        "clarification": next_clarification,
                        "missing_items": final_state.get("missing_items") or [],
                    }

                return {
                    "reply": f"Listo, agregué {product_label} al carrito.",
                    "cart": _read_session_cart(session_id, catalog),
                    "clarification": None,
                    "missing_items": [],
                }
            else:
                # Stateless: update local cart and return confirmation.
                new_cart = _upsert_local_cart_item(initial_cart, chosen_id, qty, catalog)
                validated_cart, _ = _validate_cart_with_report(new_cart, catalog)
                return {
                    "reply": f"Listo, agregué {product_label} al carrito.",
                    "cart": validated_cart,
                    "clarification": None,
                    "missing_items": [],
                }

    # ── Normal turn ────────────────────────────────────────────────────────────
    existing = None
    if session_id:
        try:
            existing = app.get_state(config)
            is_new_session = not existing.values.get("messages")
        except Exception:
            is_new_session = True
    else:
        is_new_session = True  # stateless: always rebuild full context

    if is_new_session:
        init_messages: list = [SystemMessage(content=_build_system_prompt())]
        if initial_cart:
            cart_text = "Carrito actual:\n" + "\n".join(
                f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
                for i in initial_cart
            )
            init_messages.append(SystemMessage(content=cart_text))
        if context:
            init_messages.append(SystemMessage(content=context))
        for msg in _trim_history(history):
            if msg["role"] == "user":
                init_messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                init_messages.append(AIMessage(content=msg["content"]))
        init_messages.append(HumanMessage(content=message))
        invoke_input: dict = {
            "messages": init_messages,
            "result_cart": None if session_id else (initial_cart or None),
            "clarification": None,
            "missing_items": [],
            "resolved_queries": {},
        }
    else:
        # Existing session: inject updated cart state so removals made via UI are visible,
        # then append the new human message. Checkpointer holds the rest of the history.
        if initial_cart:
            cart_lines = "\n".join(
                f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}"
                for i in initial_cart
            )
            cart_update = SystemMessage(content=f"Carrito actual (actualizado):\n{cart_lines}")
        else:
            cart_update = SystemMessage(content="Carrito actual: vacío.")

        # Loop-guard: if the previous agent turn produced no tool calls (i.e. the agent
        # responded with text instead of calling resolve_product), inject a one-line
        # reminder so it doesn't repeat the same non-tool response.
        prior_messages = (existing.values.get("messages", []) if existing is not None else [])
        last_ai = next((m for m in reversed(prior_messages) if isinstance(m, AIMessage)), None)
        agent_was_stuck = last_ai is not None and not last_ai.tool_calls

        extra_msgs = []
        if agent_was_stuck:
            extra_msgs.append(SystemMessage(content=(
                "RECORDATORIO OBLIGATORIO: Para cada producto que el usuario mencione, "
                "llamá resolve_product de inmediato. No respondas con texto sin antes "
                "llamar las herramientas. No pidas confirmación."
            )))

        invoke_input = {
            "messages": extra_msgs + [cart_update, HumanMessage(content=message)],
            "clarification": None,   # clear any stale clarification from prior turn
            "missing_items": [],     # reset per-turn
            "resolved_queries": {},  # reset per new user request
        }

    final_state = app.invoke(invoke_input, config=config)

    clarification = final_state.get("clarification")
    cart_result = (
        _read_session_cart(session_id, catalog)
        if session_id
        else final_state.get("result_cart")
    )

    return {
        "reply": _extract_reply(final_state),
        "cart": cart_result,
        "clarification": clarification,
        "missing_items": final_state.get("missing_items") or [],
    }
