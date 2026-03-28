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
    last_suggestions: list[dict]  # persisted across turns so suggestion_reply can reference them
    pending_clarification: dict | None   # issued to client; persisted in SqliteSaver
    reply: str

    # Checkout output
    forgotten_suggestion: dict | None    # {product_id, name, quantity, price, days_ago}
    checkout_result: dict | None         # {order_id, total, item_count} or {error: str}

    # User context — loaded from data/user_profile.json, read-only inside graph
    user_profile: dict


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


def _normalize_planned_items(raw_message: str, planned_items: list[dict]) -> list[dict]:
    """Coerce classifier output into stable query/quantity pairs.

    For single-item turns, quantity comes from parse_quantity(raw_message) so the
    chat path does not depend on the LLM getting the count exactly right.
    """
    from backend.product_semantic_index import parse_quantity

    normalized: list[dict] = []
    message_qty = parse_quantity(raw_message)
    single_item = len(planned_items) == 1

    for item in planned_items:
        query = str(item.get("query", "") or "").strip()
        if not query:
            continue

        try:
            quantity = max(1, int(item.get("quantity", 1)))
        except (TypeError, ValueError):
            quantity = 1

        if single_item:
            quantity = message_qty

        normalized.append({"query": query, "quantity": quantity})

    return normalized


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
    '{"turn_kind": "shopping|smalltalk|monthly_basket|suggestion_reply|suggestion_rejection|checkout", '
    '"planned_items": [{"query": "nombre producto", "quantity": 1}]}\n'
    "Para mensajes que NO son compras (saludos, preguntas generales), usá turn_kind=smalltalk "
    "y planned_items=[].\n"
    'Para generar canasta mensual, usá turn_kind=monthly_basket y planned_items=[].\n'
    "Para mensajes donde el usuario quiere confirmar/finalizar su compra, hacer checkout o pagar "
    "su carrito (ej: 'confirmar pedido', 'comprar carrito', 'finalizar compra', 'checkout', 'pagar', "
    "'quiero pagar', 'proceder con el pago'), usá turn_kind=checkout y planned_items=[].\n"
    "REGLA SUGERENCIAS: Si el último mensaje del asistente ofreció alternativas o sugerencias "
    "para un producto que no se encontró:\n"
    "- Si el usuario ACEPTA (sí, dale, ok, quiero la primera, agregá esa, etc.) o elige una opción "
    "específica → turn_kind=suggestion_reply. Si dice solo 'sí'/'dale' sin especificar cuál, "
    "planned_items=[] (se agregará la mejor opción automáticamente).\n"
    "- Si el usuario RECHAZA las sugerencias o dice que no son lo que buscaba ('eso no es X', "
    "'no quiero eso', 'no, busco otra cosa', 'eso no es lo que pedí') → turn_kind=suggestion_rejection "
    "y planned_items=[].\n"
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
    "Sugerencia aceptada genérica → "
    '{"turn_kind":"suggestion_reply","planned_items":[]}\n'
    "\n"
    'Sugerencia con elección → {"turn_kind":"suggestion_reply","planned_items":[{"query":"helado de chocolate","quantity":1}]}\n'
    "\n"
    '"eso no es helado" / "no, busco otra cosa" → '
    '{"turn_kind":"suggestion_rejection","planned_items":[]}\n'
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
        if state.get("turn_kind") in ("clarification_reply", "suggestion_reply", "suggestion_rejection"):
            return {}  # pre-set; skip re-classification

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
            planned_items = _normalize_planned_items(
                raw,
                parsed.get("planned_items", []),
            )
            return {
                "turn_kind": parsed.get("turn_kind", "smalltalk"),
                "planned_items": planned_items,
            }
        except Exception:
            _log.exception("classify_turn failed, defaulting to smalltalk")
            return {"turn_kind": "smalltalk", "planned_items": []}

    # ── Profile helpers ────────────────────────────────────────────────────────
    def _build_profile_constraints(profile: dict) -> dict:
        """Translate user_profile preferences into the constraint shape used by
        extract_constraints / _apply_hard_constraints.

        Only brand is applied here (the most common preference). Dietary
        restrictions expressed as qualifiers are appended if no query-level
        qualifier was already found.
        """
        prefs = profile.get("preferences", {})
        household = profile.get("household", {})

        # Preferred brand: use if exactly one brand is preferred and strict_brand is set
        brand: str | None = None
        if prefs.get("strict_brand"):
            preferred = prefs.get("preferred_brands", {})
            if len(preferred) == 1:
                brand = next(iter(preferred))

        # Dietary restrictions → qualifier tokens (intersect with known qualifiers)
        from backend.product_semantic_index import _QUALIFIER_TOKENS  # type: ignore[attr-defined]
        restrictions = household.get("dietary_restrictions", [])
        qualifiers = [r for r in restrictions if r in _QUALIFIER_TOKENS]

        return {"brand": brand, "size": None, "qualifiers": qualifiers}

    def _merge_constraints(query_constraints: dict, profile_constraints: dict) -> dict:
        """Merge profile constraints into query constraints.
        Query-level values take precedence (user's explicit request wins).
        """
        merged = dict(query_constraints)
        if not merged.get("brand") and profile_constraints.get("brand"):
            merged["brand"] = profile_constraints["brand"]
        profile_quals = profile_constraints.get("qualifiers", [])
        existing_quals = merged.get("qualifiers", [])
        merged["qualifiers"] = existing_quals + [q for q in profile_quals if q not in existing_quals]
        return merged

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

        profile = state.get("user_profile") or {}
        profile_constraints = _build_profile_constraints(profile)

        for item in state.get("planned_items") or []:
            query = item["query"]
            if query in already_resolved:
                continue
            qty = item.get("quantity", 1)

            from backend.product_semantic_index import extract_constraints as _extract_constraints
            query_constraints = _extract_constraints(query, catalog)
            merged = _merge_constraints(query_constraints, profile_constraints)
            verdict = _resolve_product(query, qty, catalog, constraints=merged)
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

    # ── Node: accept_suggestion ─────────────────────────────────────────────────
    def accept_suggestion(state: ShopState) -> dict:
        """Handle user accepting a previously offered suggestion.

        If planned_items has a specific query, resolve it normally.
        If planned_items is empty (generic 'si'), pick the top option from last_suggestions.
        """
        last_sug = state.get("last_suggestions") or []
        planned = state.get("planned_items") or []

        resolved_cart = []
        resolutions = []
        missing_items = []

        if planned:
            # User specified a product — resolve it normally
            for item in planned:
                query = item["query"]
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
                elif verdict["status"] == "not_found":
                    missing_items.append(query)
        elif last_sug:
            # Generic acceptance — pick top option from each suggestion group
            for sug in last_sug:
                options = sug.get("options") or []
                if options:
                    top = options[0]
                    qty = 1
                    resolution = {
                        "query": sug.get("query", top.get("name", "")),
                        "status": "resolved",
                        "quantity": qty,
                        "product": top,
                        "options": None,
                    }
                    resolutions.append(resolution)
                    resolved_cart.append(_build_cart_item(top, qty))

        return {
            "resolutions": resolutions,
            "resolved_cart": resolved_cart,
            "missing_items": missing_items,
            "suggestions": [],
            "last_suggestions": [],  # clear after acceptance
        }

    # ── Node: reject_suggestion ─────────────────────────────────────────────────
    def reject_suggestion(state: ShopState) -> dict:
        """Handle user rejecting previously offered suggestions.

        Clears last_suggestions so a subsequent 'si' won't add the rejected items.
        Builds an honest reply acknowledging the product isn't available.
        """
        last_sug = state.get("last_suggestions") or []
        rejected_queries = [s.get("query", "") for s in last_sug]
        missing = rejected_queries if rejected_queries else ["el producto solicitado"]
        return {
            "resolutions": [],
            "resolved_cart": [],
            "missing_items": missing,
            "suggestions": [],
            "last_suggestions": [],  # clear so stale suggestions can't be accepted
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

        # Forgotten item suggestion — deterministic, no LLM needed
        suggestion = state.get("forgotten_suggestion")
        if suggestion:
            days_ago = suggestion.get("days_ago", 0)
            day_str = "ayer" if days_ago <= 1 else f"hace {days_ago} días"
            name = suggestion.get("name", "ese producto")
            qty = suggestion.get("quantity", 1)
            return {"reply": (
                f"¡Espera! {day_str} compraste {name} (x{qty}) y no está en tu carrito. "
                f"¿Lo agregamos antes de confirmar, o seguimos sin él?"
            )}

        # Checkout confirmed — deterministic, no LLM needed
        checkout_result = state.get("checkout_result")
        if checkout_result:
            if checkout_result.get("error"):
                return {"reply": checkout_result["error"]}
            order_id = checkout_result.get("order_id", "")
            total = checkout_result.get("total", 0.0)
            item_count = checkout_result.get("item_count", 0)
            s = "s" if item_count != 1 else ""
            return {"reply": (
                f"¡Listo! Tu pedido fue confirmado. "
                f"Orden #{order_id} — {item_count} producto{s} por ${total:.2f}. ¡Gracias por tu compra!"
            )}

        added = [r for r in (state.get("resolutions") or []) if r.get("status") == "resolved"]
        missing = state.get("missing_items") or []

        # Build short fact string for LLM
        parts = []
        if added:
            names = ", ".join(r["product"]["name"] for r in added if r.get("product"))
            parts.append(f"Agregué al carrito: {names}.")
        if missing:
            parts.append(f"No encontré: {', '.join(missing)}.")

        # Show actual alternatives inline instead of vague "hay opciones similares"
        suggestions = state.get("suggestions") or []
        if suggestions:
            for sug in suggestions:
                query = sug.get("query", "")
                options = sug.get("options") or []
                if options:
                    option_names = ", ".join(
                        opt.get("name", "") for opt in options[:3]
                    )
                    parts.append(
                        f"No encontré {query}, pero hay alternativas: {option_names}. "
                        f"Decime si querés que agregue alguna."
                    )
                else:
                    parts.append(f"No encontré {query} ni alternativas similares.")

        facts = " ".join(parts) if parts else state.get("raw_message", "")

        verbosity = (
            (state.get("user_profile") or {})
            .get("interaction", {})
            .get("verbosity", "normal")
        )
        verbosity_instruction = {
            "brief": "Respondé en UNA sola oración, sin preguntas adicionales.",
            "normal": "Respondé de forma amable y concisa.",
            "detailed": "Incluí precio y cantidad de cada producto mencionado.",
        }.get(verbosity, "Respondé de forma amable y concisa.")

        system = (
            f"Sos un asistente de compras. Confirmá las acciones al usuario en español. "
            f"{verbosity_instruction} Sin markdown ni asteriscos. "
            "NUNCA preguntes '¿te gustaría que busque?' ni '¿querés que te muestre?'. "
            "Si hay alternativas, ya están listadas — solo preguntá cuál quiere o si quiere agregarlas. "
            "NUNCA inventes productos ni listas de opciones. Solo mencioná los productos que aparecen "
            "explícitamente en los datos. Si un producto no se encontró y no hay alternativas, "
            "simplemente decí que no lo tenemos disponible."
        )
        try:
            response = llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=facts),
            ])
            result: dict = {"reply": response.content}
        except Exception:
            # Deterministic fallback
            fallback = " ".join(parts) if parts else "Hola, ¿en qué te puedo ayudar?"
            result = {"reply": fallback}

        # Persist suggestions so suggestion_reply can reference them next turn
        if suggestions:
            result["last_suggestions"] = suggestions
        return result

    # ── Node: check_forgotten_items ───────────────────────────────────────────
    def check_forgotten_items(state: ShopState, config: RunnableConfig) -> dict:
        """Deterministic. Compares current cart against last order.
        If an item was bought before but isn't in the cart, populate forgotten_suggestion.
        Otherwise set forgotten_suggestion=None so execute_checkout fires next.
        """
        from datetime import datetime
        from backend.db import get_last_order

        sid = state.get("session_id") or (config.get("configurable") or {}).get("session_id", "")
        current_cart = _read_session_cart(sid, catalog)
        current_ids = {item["product_id"] for item in current_cart}

        last_order = get_last_order()
        if not last_order or not last_order.get("cart"):
            return {"forgotten_suggestion": None}

        missing = [
            item for item in last_order["cart"]
            if item.get("product_id") not in current_ids
        ]
        if not missing:
            return {"forgotten_suggestion": None}

        forgotten = missing[0]
        try:
            order_dt = datetime.strptime(last_order["created_at"][:19], "%Y-%m-%d %H:%M:%S")
            days_ago = (datetime.utcnow() - order_dt).days
        except Exception:
            days_ago = 0

        return {
            "forgotten_suggestion": {
                "product_id": forgotten.get("product_id", ""),
                "name": forgotten.get("name", ""),
                "quantity": forgotten.get("quantity", 1),
                "price": forgotten.get("price", 0.0),
                "days_ago": days_ago,
            }
        }

    # ── Node: execute_checkout ─────────────────────────────────────────────────
    def execute_checkout(state: ShopState, config: RunnableConfig) -> dict:
        """Deterministic. Places the order using db.place_order().
        Only reached when forgotten_suggestion is None.
        """
        from backend.db import place_order as db_place_order

        sid = state.get("session_id") or (config.get("configurable") or {}).get("session_id", "")
        current_cart = _read_session_cart(sid, catalog)

        if not current_cart:
            return {"checkout_result": {"error": "El carrito está vacío."}}

        total = round(sum(item["price"] * item["quantity"] for item in current_cart), 2)
        order_id = str(uuid.uuid4())[:8]
        db_place_order(sid, order_id, current_cart, total)

        return {
            "checkout_result": {
                "order_id": order_id,
                "total": total,
                "item_count": len(current_cart),
            }
        }

    # ── Routing ────────────────────────────────────────────────────────────────
    def route_after_classify(state: ShopState) -> str:
        turn_kind = state.get("turn_kind", "smalltalk")
        if turn_kind in ("smalltalk", "monthly_basket"):
            return "summarize"
        if turn_kind == "suggestion_reply":
            return "accept_suggestion"
        if turn_kind == "suggestion_rejection":
            return "reject_suggestion"
        if turn_kind == "checkout":
            return "check_forgotten_items"
        return "resolve_items"

    def route_after_forgotten_check(state: ShopState) -> str:
        return "summarize" if state.get("forgotten_suggestion") is not None else "execute_checkout"

    def route_after_apply(state: ShopState) -> str:
        if any(r.get("status") == "needs_clarification" for r in (state.get("resolutions") or [])):
            return "emit_clarification"
        return "summarize"

    # ── Graph wiring ───────────────────────────────────────────────────────────
    graph = StateGraph(ShopState)
    graph.add_node("classify_turn", classify_turn)
    graph.add_node("resolve_items", resolve_items)
    graph.add_node("accept_suggestion", accept_suggestion)
    graph.add_node("reject_suggestion", reject_suggestion)
    graph.add_node("apply_cart", apply_cart)
    graph.add_node("emit_clarification", emit_clarification)
    graph.add_node("check_forgotten_items", check_forgotten_items)
    graph.add_node("execute_checkout", execute_checkout)
    graph.add_node("summarize", summarize)

    graph.add_edge(START, "classify_turn")
    graph.add_conditional_edges("classify_turn", route_after_classify)
    graph.add_edge("resolve_items", "apply_cart")
    graph.add_edge("accept_suggestion", "apply_cart")
    graph.add_edge("reject_suggestion", "summarize")  # skip cart — nothing to add
    graph.add_conditional_edges("apply_cart", route_after_apply)
    graph.add_edge("emit_clarification", "summarize")
    graph.add_conditional_edges("check_forgotten_items", route_after_forgotten_check)
    graph.add_edge("execute_checkout", "summarize")
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
    from backend.user_profile import get_user_profile

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

    profile = get_user_profile()
    profile_prefs = profile.get("preferences", {})
    profile_household = profile.get("household", {})

    if not plan_row:
        # Fall back to user_profile when no recurring plan is configured in DB
        plan = {
            "household_size": profile_household.get("size", 1),
            "monthly_budget": profile_prefs.get("budget_monthly"),
            "must_haves": [],
            "preferred_brands": profile_prefs.get("preferred_brands", {}),
            "strict_brand": profile_prefs.get("strict_brand", False),
            "excluded_categories": profile_prefs.get("excluded_categories", []),
            "notes": "",
        }
        # Only generate if profile has something useful; otherwise prompt setup
        if not plan["monthly_budget"] and not plan["preferred_brands"]:
            return {
                "reply": "No tenés un plan de compras mensual guardado. Configuralo en la pestaña de compras recurrentes.",
                "proposed_cart": [],
                "cart": None,
                "clarification": None,
                "missing_items": [],
            }
    else:
        plan = {
            "household_size": plan_row["household_size"],
            "monthly_budget": plan_row["monthly_budget"],
            "must_haves": json.loads(plan_row["priority_items"]),
            "preferred_brands": json.loads(plan_row["preferred_brands"]),
            "strict_brand": bool(plan_row["strict_brand"]),
            "excluded_categories": json.loads(plan_row["excluded_categories"]),
            "notes": plan_row["notes"],
        }
        # Fill nulls from profile (DB wins, profile is fallback)
        if plan["household_size"] in (None, 0):
            plan["household_size"] = profile_household.get("size", 1)
        if plan["monthly_budget"] is None:
            plan["monthly_budget"] = profile_prefs.get("budget_monthly")
        if not plan["excluded_categories"]:
            plan["excluded_categories"] = profile_prefs.get("excluded_categories", [])

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
    from backend.user_profile import get_user_profile

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
        qty = 1

        # Retrieve the prior clarification state to validate the chosen option.
        prior_clarification: dict = {}
        if session_id:
            try:
                prior_state = app.get_state(config)
                prior_clarification = prior_state.values.get("pending_clarification") or {}
            except Exception:
                prior_clarification = {}

        prior_options = prior_clarification.get("options") or []
        valid_ids = {opt.get("id", "") for opt in prior_options}
        if valid_ids and chosen_id not in valid_ids:
            return {
                "reply": "Esa opción no es válida. Por favor elegí una de las opciones presentadas.",
                "cart": _read_session_cart(session_id, catalog) if session_id else initial_cart,
                "clarification": prior_clarification if prior_clarification else None,
                "missing_items": [],
                "clarification_response_applied": False,
            }

        if not product:
            return {
                "reply": "Esa opción ya no está disponible. Elegí otra alternativa.",
                "cart": _read_session_cart(session_id, catalog) if session_id else initial_cart,
                "clarification": prior_clarification if prior_clarification else None,
                "missing_items": [],
                "clarification_response_applied": False,
            }

        product_label = " ".join(filter(None, [product.get("name", ""), product.get("package_size", "")]))

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
                pre_resolved_cart = [
                    _build_cart_item(resolution["product"], resolution.get("quantity", 1))
                    for resolution in resolved_so_far
                    if resolution.get("status") == "resolved" and resolution.get("product")
                ]
                pre_resolved_cart.append(_build_cart_item(product, qty))
                resume_input: dict = {
                    "raw_message": pending_message,
                    "turn_kind": "clarification_reply",
                    "planned_items": stored_planned_items,
                    "resolutions": list(resolved_so_far) + [chosen_resolution],
                    "resolved_cart": pre_resolved_cart,
                    "missing_items": [],
                    "suggestions": [],
                    "last_suggestions": [],
                    "pending_clarification": None,
                    "reply": "",
                    "session_id": session_id,
                    "initial_cart": initial_cart,
                    "history": _trim_history(history),
                    "context": context or "",
                    "user_profile": get_user_profile(),
                }
                final_state = app.invoke(resume_input, config=config)
                return {
                    "reply": final_state.get("reply", f"Listo, agregué {product_label} al carrito."),
                    "cart": _read_session_cart(session_id, catalog),
                    "clarification": final_state.get("pending_clarification"),
                    "missing_items": final_state.get("missing_items") or [],
                    "suggestions": final_state.get("suggestions") or [],
                    "clarification_response_applied": True,
                }

            return {
                "reply": f"Listo, agregué {product_label} al carrito.",
                "cart": _read_session_cart(session_id, catalog),
                "clarification": None,
                "missing_items": [],
                "suggestions": [],
                "clarification_response_applied": True,
            }

        # Stateless: update local cart and return confirmation.
        new_cart = _upsert_local_cart_item(initial_cart, chosen_id, qty, catalog)
        validated_cart, _ = _validate_cart_with_report(new_cart, catalog)
        return {
            "reply": f"Listo, agregué {product_label} al carrito.",
            "cart": validated_cart,
            "clarification": None,
            "missing_items": [],
            "suggestions": [],
            "clarification_response_applied": True,
        }

    # ── Normal turn ────────────────────────────────────────────────────────────
    # Retrieve last_suggestions from prior state so suggestion_reply can reference them
    prior_suggestions: list[dict] = []
    if session_id:
        try:
            prior_state = app.get_state(config)
            prior_suggestions = prior_state.values.get("last_suggestions") or []
        except Exception:
            prior_suggestions = []

    invoke_input: dict = {
        "raw_message": message,
        "turn_kind": "",
        "planned_items": [],
        "resolutions": [],
        "resolved_cart": [],
        "missing_items": [],
        "suggestions": [],
        "last_suggestions": prior_suggestions,
        "pending_clarification": None,
        "reply": "",
        "forgotten_suggestion": None,
        "checkout_result": None,
        "session_id": session_id,
        "initial_cart": initial_cart,
        "history": _trim_history(history),
        "context": context or "",
        "user_profile": get_user_profile(),
    }

    final_state = app.invoke(invoke_input, config=config)

    # If the classifier detected monthly_basket, delegate to the dedicated handler
    if final_state.get("turn_kind") == "monthly_basket":
        return _handle_generate_basket(catalog)

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
        "checkout_result": final_state.get("checkout_result"),
    }
