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
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
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

class ShopState(TypedDict):
    # Per-turn input — set by handle_chat before invoke
    raw_message: str
    session_id: str
    initial_cart: list[dict]
    history: list[dict]
    context: str

    # Classification output
    turn_kind: str          # "shopping" | "smalltalk" | "monthly_basket" | "clarification_reply"
    planned_items: list     # [{"query": str, "quantity": int}]

    # Resolution output
    resolutions: list       # [{"query", "status", "product"|None, "options"|None, "quantity"}]
    resolved_cart: list     # _build_cart_item dicts — written to DB by apply_cart

    # Per-turn output
    missing_items: list[str]
    suggestions: list[dict]   # [{query, options: list[dict]}] for needs_suggestion items
    pending_clarification: dict | None   # issued to client; persisted in SqliteSaver
    reply: str


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return """Eres un asistente de compras para supershop, un supermercado online.

## REGLA FUNDAMENTAL
Cuando el usuario menciona cualquier producto o alimento, tu PRIMERA acción debe ser llamar a las herramientas — no respondas con texto primero. Nunca pidas confirmación antes de buscar. Nunca preguntes "¿Te gustaría que busque...?". Simplemente buscá.

## Recetas y platos — REGLA CRÍTICA
Si el usuario menciona una receta, plato o comida (ejemplos: tiramisu, pizza, tarta de manzana, milanesas, ensalada, risotto), NUNCA busques el nombre del plato como producto. En cambio:
1. Identificá mentalmente los ingredientes necesarios para esa receta.
2. Llamá resolve_product por separado para CADA ingrediente (no para el plato).
Ejemplo correcto para "quiero hacer tiramisu": llamá resolve_product("mascarpone"), resolve_product("huevos"), resolve_product("azúcar"), resolve_product("café"), resolve_product("vainillas"), resolve_product("cacao en polvo"), resolve_product("crema de leche") — uno por uno, nunca resolve_product("tiramisu").

## Flujo obligatorio para cada producto o ingrediente
1. Llamá resolve_product(query, quantity) de inmediato.
2. Si devuelve status "resolved" → llamá add_to_cart(product_id, quantity) con los datos del resultado.
3. Si devuelve status "needs_clarification" → llamá request_clarification con la pregunta y las options exactas devueltas.
4. Si devuelve status "not_found" → llamá report_missing con el nombre del producto.

## Reglas generales
- NUNCA respondas con texto sin antes haber llamado las herramientas para cada producto pedido.
- NUNCA preguntes "¿quieres que busque?", "¿te gustaría que...?", ni nada similar. Buscá directamente.
- Para pedidos con varios productos o ingredientes, resolvé cada ítem por separado con su propia llamada a resolve_product.
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


# ─── Graph ────────────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "Sos un asistente de compras. Analizá el mensaje del usuario y respondé SOLO con JSON:\n"
    '{"turn_kind": "shopping|smalltalk|monthly_basket", '
    '"planned_items": [{"query": "nombre producto", "quantity": 1}]}\n'
    "Para mensajes que NO son compras (saludos, preguntas generales), usá turn_kind=smalltalk "
    "y planned_items=[].\n"
    'Para generar canasta mensual, usá turn_kind=monthly_basket y planned_items=[].\n'
    "Extraé un ítem por producto mencionado. Resolvé números en español (dos→2, tres→3, un→1).\n"
    "\n"
    "REGLA RECETAS: Si el mensaje menciona una receta o plato, descomponélo en ingredientes "
    "individuales con queries específicos orientados al catálogo. NUNCA uses el nombre del plato "
    "como query.\n"
    "\n"
    "Ejemplos:\n"
    '{"turn_kind":"shopping","planned_items":[{"query":"leche entera","quantity":2},{"query":"yogur","quantity":1}]}\n'
    "\n"
    'Receta tiramisu → {"turn_kind":"shopping","planned_items":['
    '{"query":"queso mascarpone","quantity":1},'
    '{"query":"cafe instantaneo","quantity":1},'
    '{"query":"huevos","quantity":1},'
    '{"query":"azucar","quantity":1},'
    '{"query":"vainillas","quantity":1},'
    '{"query":"cacao en polvo","quantity":1},'
    '{"query":"crema de leche","quantity":1}]}\n'
    "\n"
    'Receta pizza → {"turn_kind":"shopping","planned_items":['
    '{"query":"harina","quantity":1},'
    '{"query":"queso mozzarella","quantity":1},'
    '{"query":"salsa de tomate","quantity":1}]}\n'
    "\n"
    "SOLO JSON, sin markdown ni texto adicional."
)


def _build_graph(catalog: list[dict], api_key: str):
    """Compile phase-based graph: classify → resolve → apply/clarify → summarize."""
    from backend.product_semantic_index import resolve_product as _resolve_product

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=api_key,
        timeout=30,
    )

    # ── Node: classify_turn ────────────────────────────────────────────────────
    def classify_turn(state: ShopState) -> dict:
        """LLM classifies intent and extracts planned items in one structured call."""
        if state.get("turn_kind") == "clarification_reply":
            return {}  # pre-set by handle_chat; skip re-classification

        raw = state.get("raw_message", "")
        history = state.get("history") or []

        history_text = ""
        for msg in history[-6:]:
            role = "Usuario" if msg.get("role") == "user" else "Asistente"
            history_text += f"{role}: {msg.get('content', '')}\n"

        user_content = f"Historial:\n{history_text}\nMensaje: {raw}" if history_text else f"Mensaje: {raw}"
        if state.get("context"):
            user_content = f"Contexto: {state['context']}\n{user_content}"

        try:
            response = llm.invoke([
                SystemMessage(content=_CLASSIFY_SYSTEM),
                HumanMessage(content=user_content),
            ])
            parsed = json.loads(response.content)
            return {
                "turn_kind": parsed.get("turn_kind", "smalltalk"),
                "planned_items": parsed.get("planned_items", []),
            }
        except Exception:
            _log.exception("classify_turn failed, defaulting to smalltalk")
            return {"turn_kind": "smalltalk", "planned_items": []}

    # ── Node: resolve_items ────────────────────────────────────────────────────
    def resolve_items(state: ShopState) -> dict:
        """Deterministic: call resolve_product() for each planned item.
        Resolve-first: processes ALL items before returning.
        At most one needs_clarification is kept; extras go to missing_items.
        needs_suggestion items are collected separately so the UI can offer them
        without blocking the conversation.
        """
        resolutions = list(state.get("resolutions") or [])
        resolved_cart = list(state.get("resolved_cart") or [])
        missing_items = list(state.get("missing_items") or [])
        suggestions = list(state.get("suggestions") or [])

        already_resolved = {r["query"] for r in resolutions}
        has_clarification = any(
            r.get("status") == "needs_clarification" for r in resolutions
        )

        for item in state.get("planned_items") or []:
            query = item["query"]
            if query in already_resolved:
                continue
            qty = item.get("quantity", 1)

            verdict = _resolve_product(query, qty, catalog)
            resolution = {
                "query": query,
                "status": verdict["status"],
                "quantity": qty,
                "product": verdict.get("product"),
                "options": verdict.get("options"),
            }
            resolutions.append(resolution)

            if verdict["status"] == "resolved":
                resolved_cart.append(_build_cart_item(verdict["product"], qty))
            elif verdict["status"] == "needs_clarification":
                if has_clarification:
                    # Already have one clarification pending — defer this to missing
                    missing_items.append(query)
                else:
                    has_clarification = True
                    # Keep in resolutions (emit_clarification will pick it up)
            elif verdict["status"] == "needs_suggestion":
                suggestions.append({
                    "query": query,
                    "reason": "not_found",
                    "options": verdict.get("options", []),
                })
            elif verdict["status"] == "not_found":
                missing_items.append(query)

        return {
            "resolutions": resolutions,
            "resolved_cart": resolved_cart,
            "missing_items": missing_items,
            "suggestions": suggestions,
        }

    # ── Node: apply_cart ───────────────────────────────────────────────────────
    def apply_cart(state: ShopState, config: RunnableConfig) -> dict:
        """Persist resolved_cart items to DB. Idempotent upsert, no LLM."""
        sid = state.get("session_id") or (config.get("configurable") or {}).get("session_id", "")
        if sid and state.get("resolved_cart"):
            items = [(item["product_id"], item["quantity"]) for item in state["resolved_cart"]]
            _write_session_cart_items(sid, items)
        return {}

    # ── Node: emit_clarification ───────────────────────────────────────────────
    def emit_clarification(state: ShopState) -> dict:
        """Format pending_clarification for the first needs_clarification resolution."""
        unresolved = next(
            (r for r in (state.get("resolutions") or [])
             if r.get("status") == "needs_clarification"),
            None,
        )
        if not unresolved:
            return {}

        pending = {
            "question": f"¿Cuál {unresolved['query']} preferís?",
            "options": unresolved["options"],
            "pending_request_id": str(uuid.uuid4()),
            "pending_message": state.get("raw_message", ""),
            "original_query": unresolved["query"],
            # Stored so handle_chat can resume after clarification without a re-classify call
            "planned_items": state.get("planned_items") or [],
            "resolved_so_far": [
                r for r in (state.get("resolutions") or [])
                if r.get("status") == "resolved"
            ],
        }
        return {"pending_clarification": pending}

    # ── Node: summarize ────────────────────────────────────────────────────────
    def summarize(state: ShopState) -> dict:
        """Generate natural-language reply. Falls back to deterministic template on error."""
        pending = state.get("pending_clarification")
        if pending:
            return {"reply": pending["question"]}

        added = [r for r in (state.get("resolutions") or []) if r.get("status") == "resolved"]
        missing = state.get("missing_items") or []

        # Build short fact string for LLM
        parts = []
        if added:
            names = ", ".join(r["product"]["name"] for r in added if r.get("product"))
            parts.append(f"Agregué: {names}.")
        if missing:
            parts.append(f"No encontré: {', '.join(missing)}.")

        suggestions = state.get("suggestions") or []
        if suggestions:
            sug_names = ", ".join(s["query"] for s in suggestions)
            parts.append(f"No tengo exactamente: {sug_names}. Hay opciones similares disponibles.")

        facts = " ".join(parts) if parts else state.get("raw_message", "")

        system = (
            "Sos un asistente de compras. Confirmá las acciones al usuario en español, "
            "de forma amable y concisa. Sin markdown ni asteriscos."
        )
        try:
            response = llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=facts),
            ])
            return {"reply": response.content}
        except Exception:
            # Deterministic fallback
            fallback = " ".join(parts) if parts else "Hola, ¿en qué te puedo ayudar?"
            if added and fallback:
                fallback += " ¿Está bien así?"
            return {"reply": fallback}

    # ── Routing ────────────────────────────────────────────────────────────────
    def route_after_classify(state: ShopState) -> str:
        turn_kind = state.get("turn_kind", "smalltalk")
        if turn_kind in ("smalltalk", "monthly_basket"):
            return "summarize"
        return "resolve_items"

    def route_after_apply(state: ShopState) -> str:
        if any(r.get("status") == "needs_clarification" for r in (state.get("resolutions") or [])):
            return "emit_clarification"
        return "summarize"

    # ── Graph wiring ───────────────────────────────────────────────────────────
    graph = StateGraph(ShopState)
    graph.add_node("classify_turn", classify_turn)
    graph.add_node("resolve_items", resolve_items)
    graph.add_node("apply_cart", apply_cart)
    graph.add_node("emit_clarification", emit_clarification)
    graph.add_node("summarize", summarize)

    graph.add_edge(START, "classify_turn")
    graph.add_conditional_edges("classify_turn", route_after_classify)
    graph.add_edge("resolve_items", "apply_cart")
    graph.add_conditional_edges("apply_cart", route_after_apply)
    graph.add_edge("emit_clarification", "summarize")
    graph.add_edge("summarize", END)

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
    basket_result = generate_monthly_basket_candidates(
        prefs=plan,
        order_history=order_history,
        catalog=catalog,
        budget=budget,
    )
    candidates = basket_result["candidates"]
    budget_overflow = basket_result["budget_overflow"]

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

    overflow_note = " ⚠️ El presupuesto no alcanza para cubrir todos los productos esenciales." if budget_overflow else ""
    reply = (
        f"Generé tu canasta mensual con {len(proposed_cart)} productos por ${total:.2f}. "
        f"{must_haves} esenciales, {recurring} recurrentes, {offers} con descuento. "
        f"Revisá el detalle y confirmá cuando quieras.{overflow_note}"
    )

    return {
        "reply": reply,
        "proposed_cart": proposed_cart,
        "cart": None,
        "clarification": None,
        "missing_items": [],
        "budget_overflow": budget_overflow,
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

            # Retrieve the prior clarification state to validate the chosen option.
            prior_clarification: dict = {}
            if session_id:
                try:
                    prior_state = app.get_state(config)
                    prior_clarification = prior_state.values.get("pending_clarification") or {}

                    # Validate chosen_id against the options that were actually presented.
                    prior_options = prior_clarification.get("options") or []
                    valid_ids = {opt.get("id", "") for opt in prior_options}
                    if valid_ids and chosen_id not in valid_ids:
                        return {
                            "reply": "Esa opción no es válida. Por favor elegí una de las opciones presentadas.",
                            "cart": _read_session_cart(session_id, catalog),
                            "clarification": prior_clarification if prior_clarification else None,
                            "missing_items": [],
                        }
                except Exception:
                    pass

            if session_id:
                _write_session_cart_item(session_id, chosen_id, qty)

                pending_message = prior_clarification.get("pending_message", "")
                stored_planned_items = prior_clarification.get("planned_items") or []
                resolved_so_far = prior_clarification.get("resolved_so_far") or []

                if pending_message and stored_planned_items:
                    # Resume: inject chosen product + prior resolved items, then continue
                    chosen_resolution = {
                        "query": prior_clarification.get("original_query", chosen_id),
                        "status": "resolved",
                        "product": product,
                        "quantity": qty,
                        "options": None,
                    }
                    resume_input: dict = {
                        "raw_message": pending_message,
                        "turn_kind": "clarification_reply",
                        "planned_items": stored_planned_items,
                        "resolutions": list(resolved_so_far) + [chosen_resolution],
                        "resolved_cart": [],
                        "missing_items": [],
                        "suggestions": [],
                        "pending_clarification": None,
                        "reply": "",
                        "session_id": session_id,
                        "initial_cart": initial_cart,
                        "history": _trim_history(history),
                        "context": context or "",
                    }
                    final_state = app.invoke(resume_input, config=config)
                    return {
                        "reply": final_state.get("reply", f"Listo, agregué {product_label} al carrito."),
                        "cart": _read_session_cart(session_id, catalog),
                        "clarification": final_state.get("pending_clarification"),
                        "missing_items": final_state.get("missing_items") or [],
                        "suggestions": final_state.get("suggestions") or [],
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
    invoke_input: dict = {
        "raw_message": message,
        "turn_kind": "",
        "planned_items": [],
        "resolutions": [],
        "resolved_cart": [],
        "missing_items": [],
        "suggestions": [],
        "pending_clarification": None,
        "reply": "",
        "session_id": session_id,
        "initial_cart": initial_cart,
        "history": _trim_history(history),
        "context": context or "",
    }

    final_state = app.invoke(invoke_input, config=config)

    clarification = final_state.get("pending_clarification")
    cart_result = (
        _read_session_cart(session_id, catalog)
        if session_id
        else (final_state.get("resolved_cart") or initial_cart or None)
    )

    return {
        "reply": final_state.get("reply", ""),
        "cart": cart_result,
        "clarification": clarification,
        "missing_items": final_state.get("missing_items") or [],
        "suggestions": final_state.get("suggestions") or [],
    }
