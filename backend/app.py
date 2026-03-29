"""
Route dispatch, request parsing, response envelope.
Owner: Nacho

All responses use the envelope:
    { "ok": bool, "data": any, "error": str|null, "request_id": str }
"""

import json
import logging
import sys
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

from backend.db import (
    get_db,
    get_session_cart,
    upsert_session_cart_item,
    remove_session_cart_item,
    place_order,
    pending_clarification_exists,
    save_pending_clarification,
    resolve_pending_clarification,
)

# Fail fast if Juan's function is missing — no silent fallback
from backend.product_semantic_index import generate_monthly_basket_candidates

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("supershop")

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


def _load_catalog() -> list:
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH) as f:
        return json.load(f)


def _validate_order_cart(cart: list, catalog: list) -> tuple[list, list]:
    """Validate cart items against catalog.
    Returns (validated_items, dropped_item_names).
    Drops unknowns and out-of-stock; enforces catalog prices.
    """
    catalog_map = {p["id"]: p for p in catalog}
    validated = []
    dropped = []
    for item in cart:
        pid = item.get("product_id")
        product = catalog_map.get(pid)
        if not product:
            if item.get("name"):
                dropped.append(item["name"])
            continue
        if product.get("available_quantity", 0) == 0:
            dropped.append(product.get("name", pid))
            continue
        try:
            qty = max(1, int(item.get("quantity", 1)))
        except (ValueError, TypeError):
            qty = 1
        validated.append({
            "product_id": pid,
            "name": product.get("name", ""),
            "brand": product.get("brand", ""),
            "package_size": product.get("package_size", ""),
            "price": product.get("price", 0.0),
            "quantity": qty,
            "image_url": product.get("image_url", ""),
        })
    return validated, dropped


def _assemble_chat_context() -> str | None:
    """Build a context string from recent order history and saved preferences."""
    parts = []
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT cart_json, total, created_at FROM orders ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            pref_row = conn.execute(
                "SELECT prefs_json FROM preferences WHERE key='default'"
            ).fetchone()
        finally:
            conn.close()

        if rows:
            lines = ["Historial de compras recientes del usuario:"]
            for r in rows:
                items = json.loads(r["cart_json"])
                names = ", ".join(f"{i['name']} x{i['quantity']}" for i in items[:5])
                suffix = f" (y {len(items)-5} más)" if len(items) > 5 else ""
                lines.append(f"- {r['created_at'][:10]}: {names}{suffix} — total ${r['total']:.2f}")
            parts.append("\n".join(lines))

        if pref_row:
            prefs = json.loads(pref_row["prefs_json"])
            pref_lines = []
            if prefs.get("notes"):
                pref_lines.append(f"Notas del usuario: {prefs['notes']}")
            if prefs.get("excluded_categories"):
                pref_lines.append(f"Categorías excluidas: {', '.join(prefs['excluded_categories'])}")
            if prefs.get("preferred_brands"):
                brands = ", ".join(f"{k}: {v}" for k, v in prefs["preferred_brands"].items())
                pref_lines.append(f"Marcas preferidas: {brands}")
            if pref_lines:
                parts.append("Preferencias del usuario:\n" + "\n".join(pref_lines))
    except Exception:
        pass  # context is best-effort, never block a chat turn

    return "\n\n".join(parts) if parts else None


def _validate_clarification_response(raw) -> dict | None:
    """Validate and normalize a clarification_response payload.
    Returns a clean dict or None if absent/malformed."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _log.warning("clarification_response is not a dict, ignoring: %r", type(raw).__name__)
        return None
    pid = raw.get("pending_request_id")
    cid = raw.get("chosen_option_id")
    if not isinstance(pid, str) or not pid.strip():
        _log.warning("clarification_response missing valid pending_request_id, ignoring")
        return None
    if not isinstance(cid, str) or not cid.strip():
        _log.warning("clarification_response missing valid chosen_option_id, ignoring")
        return None
    result = {"pending_request_id": pid.strip(), "chosen_option_id": cid.strip()}
    pending_msg = raw.get("pending_message")
    if isinstance(pending_msg, str) and pending_msg.strip():
        result["pending_message"] = pending_msg.strip()
    return result


def _validate_chat_body(body: dict) -> tuple:
    """Validate and normalize POST /api/v1/chat body.
    Returns (message, history, cart, clarification_response, error_str|None)."""
    message = body.get("message", "")
    if not isinstance(message, str):
        return "", [], [], None, "message must be a string"
    message = message.strip()

    history = body.get("history", [])
    if not isinstance(history, list):
        _log.warning("history is not a list, discarding")
        history = []
    history = [
        h for h in history
        if isinstance(h, dict)
        and isinstance(h.get("role"), str)
        and isinstance(h.get("content"), str)
    ]

    cart = body.get("cart", [])
    if not isinstance(cart, list):
        _log.warning("cart is not a list, discarding")
        cart = []
    cart = [c for c in cart if isinstance(c, dict) and c.get("product_id")]

    clarification_response = _validate_clarification_response(body.get("clarification_response"))
    return message, history, cart, clarification_response, None


def envelope(data=None, error=None, request_id=None):
    return {
        "ok": error is None,
        "data": data if data is not None else {},
        "error": error,
        "request_id": request_id or str(uuid.uuid4()),
    }


class RequestHandler(BaseHTTPRequestHandler):
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-request — nothing to do

    def log_message(self, format, *args):
        _log.info("%s %s", self.command, self.path)

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected — nothing to do

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/v1/catalog":
            self._handle_catalog()
        elif path == "/api/v1/orders":
            self._handle_orders_get()
        elif path == "/api/v1/preferences":
            self._handle_preferences_get()
        elif path == "/api/v1/cart":
            self._handle_cart_get(parsed.query)
        elif path == "/api/v1/recurring-plan":
            self._handle_recurring_plan_get()
        elif path == "/dashboard":
            self._serve_static("/dashboard.html")
        elif path.startswith("/"):
            # Serve static frontend files
            self._serve_static(path)
        else:
            self.send_json(envelope(error="Not found"), 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Binary uploads — must not pass through _read_body()
        if path == "/api/v1/transcribe":
            self._handle_transcribe()
            return
        if path == "/api/v1/describe-image":
            self._handle_describe_image()
            return

        body = self._read_body()

        if path == "/api/v1/chat":
            self._handle_chat(body)
        elif path == "/api/v1/auth/register":
            self._handle_auth_register(body)
        elif path == "/api/v1/auth/login":
            self._handle_auth_login(body)
        elif path == "/api/v1/orders":
            self._handle_orders_post(body)
        elif path == "/api/v1/preferences":
            self._handle_preferences_put(body)
        elif path == "/api/v1/cart/remove":
            self._handle_cart_remove(body)
        elif path == "/api/v1/cart/sync":
            self._handle_cart_sync(body)
        elif path == "/api/v1/cart":
            self._handle_cart_add(body)
        elif path == "/api/v1/recurring-plan/generate":
            self._handle_recurring_plan_generate()
        elif path == "/api/v1/recurring-plan/accept":
            self._handle_recurring_plan_accept(body)
        elif path == "/api/v1/recurring-plan":
            self._handle_recurring_plan_put(body)
        else:
            self.send_json(envelope(error="Not found"), 404)

    # ─── Handlers ────────────────────────────────────────────────────────────

    def _handle_catalog(self):
        try:
            if not CATALOG_PATH.exists():
                self.send_json(envelope(data={"products": [], "total": 0}))
                return
            with open(CATALOG_PATH) as f:
                products = json.load(f)
            self.send_json(envelope(data={"products": products, "total": len(products)}))
        except Exception as e:
            _log.error("catalog error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    # ─── Chat (Phase 4: split into normalize / dispatch / build-response) ─────

    def _normalize_chat_request(self, body: dict) -> tuple:
        """Validate and coerce all request fields. Returns (message, history, cart, clarification_response, session_id, action, error)."""
        message, history, cart, clarification_response, err = _validate_chat_body(body)
        if err:
            return None, None, None, None, None, None, err
        session_id = str(body.get("session_id", "") or self.headers.get("X-Session-Token", "") or "")
        action = str(body.get("action", "") or "")
        return message, history, cart, clarification_response, session_id, action, None

    def _dispatch_chat(self, message, history, cart, clarification_response, session_id, action, req_id) -> dict:
        """Call handle_chat() and return its raw result dict."""
        from backend.chat_agent_agentic import handle_chat
        return handle_chat(
            message=message,
            history=history,
            cart=cart,
            clarification_response=clarification_response,
            context=_assemble_chat_context(),
            session_id=session_id,
            action=action or None,
        )

    def _build_chat_response(self, result: dict, session_id: str, dropped_items: list, catalog: list) -> dict:
        """Hydrate server cart, add dropped_items, return final data dict.
        Catalog is passed in to avoid a second disk read (already loaded by the caller).
        """
        result.pop("clarification_response_applied", None)
        if result.get("cart") is not None:
            server_cart = get_session_cart(session_id, catalog) if session_id else result["cart"]
            result["cart"] = server_cart
        result["dropped_items"] = dropped_items
        return result

    def _handle_chat(self, body: dict):
        req_id = str(uuid.uuid4())
        message, history, cart, clarification_response, session_id, action, err = self._normalize_chat_request(body)
        if err:
            _log.warning("chat [%s] bad request: %s", req_id, err)
            self.send_json(envelope(error=err, request_id=req_id), 400)
            return

        # Load catalog once — reused for cart validation and response hydration
        catalog = _load_catalog()
        _, dropped_items = _validate_order_cart(cart, catalog)

        # Phase 2: validate clarification_response against persisted state
        if clarification_response and session_id:
            pid = clarification_response["pending_request_id"]
            if not pending_clarification_exists(session_id, pid):
                _log.warning("chat [%s] stale or unknown clarification pending_request_id=%r", req_id, pid)
                self.send_json(envelope(error="stale or unknown clarification request", request_id=req_id), 400)
                return

        try:
            _log.info("chat [%s] message=%r clarification=%s", req_id, message[:60], clarification_response is not None)
            result = self._dispatch_chat(message, history, cart, clarification_response, session_id, action, req_id)

            if clarification_response and session_id and result.get("clarification_response_applied"):
                pid = clarification_response["pending_request_id"]
                if not resolve_pending_clarification(session_id, pid):
                    _log.warning("chat [%s] clarification resolved concurrently pending_request_id=%r", req_id, pid)
                    self.send_json(envelope(error="stale or unknown clarification request", request_id=req_id), 400)
                    return

            # Phase 2: persist issued clarification
            clarification = result.get("clarification")
            if clarification and session_id:
                save_pending_clarification(
                    session_id=session_id,
                    pending_request_id=clarification.get("pending_request_id", ""),
                    original_query=clarification.get("question", ""),
                    options=[o.get("id", "") for o in (clarification.get("options") or [])],
                )

            data = self._build_chat_response(result, session_id, dropped_items, catalog)
            _log.info("chat [%s] ok cart_items=%d clarification=%s dropped=%d",
                      req_id, len(data.get("cart") or []), data.get("clarification") is not None, len(dropped_items))
            self.send_json(envelope(data=data, request_id=req_id))
        except Exception as e:
            _log.error("chat [%s] error: %s", req_id, e, exc_info=True)
            self.send_json(envelope(error=str(e), request_id=req_id), 500)

    def _handle_auth_register(self, body: dict):
        # NOT IMPLEMENTED — auth is stubbed until after the hackathon
        self.send_json(envelope(error="Not implemented"), 501)

    def _handle_auth_login(self, body: dict):
        # NOT IMPLEMENTED — auth is stubbed until after the hackathon
        self.send_json(envelope(error="Not implemented"), 501)

    def _handle_orders_get(self):
        try:
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT id, cart_json, total, created_at FROM orders ORDER BY created_at DESC"
                ).fetchall()
            finally:
                conn.close()
            orders = []
            for r in rows:
                try:
                    items = json.loads(r["cart_json"])
                except Exception:
                    items = []
                orders.append({
                    "id": r["id"],
                    "items": items,
                    "total": r["total"],
                    "created_at": r["created_at"],
                })
            self.send_json(envelope(data={"orders": orders}))
        except Exception as e:
            _log.error("orders GET error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_orders_post(self, body: dict):
        """Bug #3 fix: read cart from server session_carts, not request body."""
        session_id = str(body.get("session_id", "") or self.headers.get("X-Session-Token", "") or "")
        if not session_id:
            self.send_json(envelope(error="session_id or X-Session-Token required"), 400)
            return
        try:
            catalog = _load_catalog()
            cart = get_session_cart(session_id, catalog)
            if not cart:
                self.send_json(envelope(error="session cart is empty"), 400)
                return
            total = round(sum(i["price"] * i["quantity"] for i in cart), 2)
            order_id = str(uuid.uuid4())[:8]
            place_order(session_id, order_id, cart, total)
            _log.info("order [%s] placed items=%d total=%.2f", order_id, len(cart), total)
            self.send_json(envelope(data={"order_id": order_id, "total": total}))
        except Exception as e:
            _log.error("orders POST error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_preferences_get(self):
        try:
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT prefs_json, updated_at FROM preferences WHERE key='default'"
                ).fetchone()
            finally:
                conn.close()
            prefs = json.loads(row["prefs_json"]) if row else {}
            updated_at = row["updated_at"] if row else None
            self.send_json(envelope(data={"preferences": prefs, "updated_at": updated_at}))
        except Exception as e:
            self.send_json(envelope(error=str(e)), 500)

    def _handle_preferences_put(self, body: dict):
        try:
            prefs = body.get("preferences", {})
            if not isinstance(prefs, dict):
                self.send_json(envelope(error="preferences must be an object"), 400)
                return
            conn = get_db()
            try:
                conn.execute(
                    """INSERT INTO preferences (key, prefs_json, updated_at)
                       VALUES ('default', ?, datetime('now'))
                       ON CONFLICT(key) DO UPDATE SET
                           prefs_json = excluded.prefs_json,
                           updated_at = excluded.updated_at""",
                    (json.dumps(prefs),),
                )
                conn.commit()
            finally:
                conn.close()
            self.send_json(envelope(data={"preferences": prefs}))
        except Exception as e:
            self.send_json(envelope(error=str(e)), 500)

    # ─── Session cart handlers ────────────────────────────────────────────────

    def _handle_cart_get(self, query_string: str):
        params = parse_qs(query_string)
        session_id = (params.get("session_id") or [None])[0]
        if not session_id:
            self.send_json(envelope(error="session_id query param required"), 400)
            return
        try:
            rows_conn = get_db()
            try:
                rows = rows_conn.execute(
                    "SELECT product_id, quantity FROM session_carts WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
            finally:
                rows_conn.close()
            items = [{"product_id": r["product_id"], "quantity": r["quantity"]} for r in rows]
            self.send_json(envelope(data={"session_id": session_id, "items": items}))
        except Exception as e:
            _log.error("cart GET error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_cart_add(self, body: dict):
        """Bug #4 fix entry point — uses EXCLUSIVE transaction via upsert_session_cart_item."""
        session_id = body.get("session_id", "")
        product_id = body.get("product_id", "")
        if not session_id or not product_id:
            self.send_json(envelope(error="session_id and product_id required"), 400)
            return
        try:
            qty = max(1, int(body.get("quantity", 1)))
        except (ValueError, TypeError):
            qty = 1
        try:
            upsert_session_cart_item(session_id, product_id, qty)
            self.send_json(envelope(data={"session_id": session_id, "product_id": product_id, "quantity": qty}))
        except Exception as e:
            _log.error("cart add error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_cart_remove(self, body: dict):
        """Uses EXCLUSIVE transaction via remove_session_cart_item."""
        session_id = body.get("session_id", "")
        product_id = body.get("product_id", "")
        if not session_id or not product_id:
            self.send_json(envelope(error="session_id and product_id required"), 400)
            return
        try:
            remove_session_cart_item(session_id, product_id)
            self.send_json(envelope(data={"removed": True}))
        except Exception as e:
            _log.error("cart remove error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_cart_sync(self, body: dict):
        """Replace session cart with the provided items list (bulk write)."""
        session_id = str(body.get("session_id", "") or self.headers.get("X-Session-Token", "") or "")
        if not session_id:
            self.send_json(envelope(error="session_id or X-Session-Token required"), 400)
            return
        items = body.get("items", [])
        if not isinstance(items, list):
            self.send_json(envelope(error="items must be a list"), 400)
            return
        try:
            conn = get_db()
            try:
                conn.execute("DELETE FROM session_carts WHERE session_id = ?", (session_id,))
                for item in items:
                    pid = str(item.get("product_id", ""))
                    qty = max(1, int(item.get("quantity", 1)))
                    if pid:
                        conn.execute(
                            "INSERT OR REPLACE INTO session_carts (session_id, product_id, quantity) VALUES (?, ?, ?)",
                            (session_id, pid, qty),
                        )
                conn.commit()
            finally:
                conn.close()
            self.send_json(envelope(data={"synced": len(items)}))
        except Exception as e:
            _log.error("cart sync error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    # ─── Recurring plan handlers ──────────────────────────────────────────────

    def _handle_recurring_plan_get(self):
        try:
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT * FROM recurring_plans WHERE id='default'"
                ).fetchone()
            finally:
                conn.close()
            if row:
                plan = {
                    "household_size": row["household_size"],
                    "monthly_budget": row["monthly_budget"],
                    "priority_items": json.loads(row["priority_items"]),
                    "preferred_brands": json.loads(row["preferred_brands"]),
                    "strict_brand": bool(row["strict_brand"]),
                    "excluded_categories": json.loads(row["excluded_categories"]),
                    "notes": row["notes"],
                    "updated_at": row["updated_at"],
                }
            else:
                plan = {}
            self.send_json(envelope(data={"plan": plan}))
        except Exception as e:
            _log.error("recurring plan GET error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_recurring_plan_put(self, body: dict):
        plan = body.get("plan", {})
        if not isinstance(plan, dict):
            self.send_json(envelope(error="plan must be an object"), 400)
            return
        try:
            household_size = max(1, int(plan.get("household_size", 1)))
        except (ValueError, TypeError):
            household_size = 1
        try:
            monthly_budget = float(plan["monthly_budget"]) if plan.get("monthly_budget") is not None else None
        except (ValueError, TypeError):
            monthly_budget = None
        priority_items = plan.get("priority_items", [])
        if not isinstance(priority_items, list):
            priority_items = []
        preferred_brands = plan.get("preferred_brands", {})
        if not isinstance(preferred_brands, dict):
            preferred_brands = {}
        strict_brand = bool(plan.get("strict_brand", False))
        excluded_categories = plan.get("excluded_categories", [])
        if not isinstance(excluded_categories, list):
            excluded_categories = []
        notes = str(plan.get("notes", ""))
        try:
            conn = get_db()
            try:
                conn.execute(
                    """INSERT INTO recurring_plans
                           (id, household_size, monthly_budget, priority_items,
                            preferred_brands, strict_brand, excluded_categories, notes, updated_at)
                       VALUES ('default', ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                       ON CONFLICT(id) DO UPDATE SET
                           household_size      = excluded.household_size,
                           monthly_budget      = excluded.monthly_budget,
                           priority_items      = excluded.priority_items,
                           preferred_brands    = excluded.preferred_brands,
                           strict_brand        = excluded.strict_brand,
                           excluded_categories = excluded.excluded_categories,
                           notes               = excluded.notes,
                           updated_at          = excluded.updated_at""",
                    (household_size, monthly_budget, json.dumps(priority_items),
                     json.dumps(preferred_brands), int(strict_brand),
                     json.dumps(excluded_categories), notes),
                )
                conn.commit()
            finally:
                conn.close()
            self.send_json(envelope(data={"plan": {
                "household_size": household_size,
                "monthly_budget": monthly_budget,
                "priority_items": priority_items,
                "preferred_brands": preferred_brands,
                "strict_brand": strict_brand,
                "excluded_categories": excluded_categories,
                "notes": notes,
            }}))
        except Exception as e:
            _log.error("recurring plan PUT error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_recurring_plan_generate(self):
        """Phase 3: calls generate_monthly_basket_candidates() — no stub fallback."""
        try:
            conn = get_db()
            try:
                plan_row = conn.execute(
                    "SELECT * FROM recurring_plans WHERE id='default'"
                ).fetchone()
                order_rows = conn.execute(
                    "SELECT cart_json FROM orders ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
            finally:
                conn.close()

            plan: dict = {}
            if plan_row:
                plan = {
                    "household_size": plan_row["household_size"],
                    "monthly_budget": plan_row["monthly_budget"],
                    # map priority_items → must_haves (Juan's function key)
                    "must_haves": json.loads(plan_row["priority_items"]),
                    "preferred_brands": json.loads(plan_row["preferred_brands"]),
                    "strict_brand": bool(plan_row["strict_brand"]),
                    "excluded_categories": json.loads(plan_row["excluded_categories"]),
                    "notes": plan_row["notes"],
                }

            order_history: list = []
            for r in order_rows:
                try:
                    items = json.loads(r["cart_json"])
                except Exception:
                    items = []
                order_history.append(items if isinstance(items, list) else [])

            catalog = _load_catalog()
            budget = plan.get("monthly_budget") or float("inf")

            budget_overflow = False
            basket_result = generate_monthly_basket_candidates(
                prefs=plan,
                order_history=order_history,
                catalog=catalog,
                budget=budget,
            )
            candidates = basket_result["candidates"]
            budget_overflow = basket_result["budget_overflow"]

            proposed_cart = []
            for c in candidates:
                product = c.get("product")
                if not product:
                    continue
                try:
                    qty = max(1, int(c.get("quantity", 1)))
                except (ValueError, TypeError):
                    qty = 1
                proposed_cart.append({
                    "product_id": product["id"],
                    "name": product.get("name", ""),
                    "brand": product.get("brand", ""),
                    "package_size": product.get("package_size", ""),
                    "price": product.get("price", 0.0),
                    "quantity": qty,
                    "image_url": product.get("image_url", ""),
                    "tag": c.get("tag", "suggested"),
                })

            total = round(sum(i["price"] * i["quantity"] for i in proposed_cart), 2)
            _log.info("recurring-plan generate: %d items total=%.2f exceeded=%s",
                      len(proposed_cart), total, budget_overflow)
            self.send_json(envelope(data={
                "proposed_cart": proposed_cart,
                "total": total,
                "budget_exceeded": budget_overflow,
            }))
        except Exception as e:
            _log.error("recurring plan generate error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _handle_recurring_plan_accept(self, body: dict):
        proposed_cart = body.get("proposed_cart", [])
        if not isinstance(proposed_cart, list):
            self.send_json(envelope(error="proposed_cart must be a list"), 400)
            return
        try:
            catalog = _load_catalog()
            validated, _ = _validate_order_cart(proposed_cart, catalog)
            total = round(sum(i["price"] * i["quantity"] for i in validated), 2)
            order_id = str(uuid.uuid4())[:8]
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO orders (id, cart_json, total) VALUES (?, ?, ?)",
                    (order_id, json.dumps(validated), total),
                )
                conn.commit()
            finally:
                conn.close()
            _log.info("recurring plan accepted as order [%s] items=%d total=%.2f", order_id, len(validated), total)
            self.send_json(envelope(data={"order_id": order_id, "total": total, "items": len(validated)}))
        except Exception as e:
            _log.error("recurring plan accept error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    # ─── Audio transcription ──────────────────────────────────────────────────

    def _handle_transcribe(self):
        import os
        import tempfile
        content_type = self.headers.get("Content-Type", "audio/webm")
        if "wav" in content_type:
            ext = ".wav"
        elif "mp4" in content_type or "m4a" in content_type:
            ext = ".mp4"
        elif "ogg" in content_type:
            ext = ".ogg"
        else:
            ext = ".webm"

        raw = self._read_raw_body(max_bytes=25_000_000)
        if not raw:
            self.send_json(envelope(error="audio body required (max 25 MB)"), 400)
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(raw)
                tmp_path = f.name

            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="es",
                )
            text = transcript.text or ""
            _log.info("transcribe: %r", text[:80])
            self.send_json(envelope(data={"text": text}))
        except Exception as e:
            _log.error("transcribe error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ─── Image description (vision) ──────────────────────────────────────────

    def _handle_describe_image(self):
        import base64
        import os
        content_type = self.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        allowed = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if content_type not in allowed:
            self.send_json(envelope(error=f"unsupported image type: {content_type}"), 400)
            return

        raw = self._read_raw_body(max_bytes=20_000_000)
        if not raw:
            self.send_json(envelope(error="image body required (max 20 MB)"), 400)
            return

        try:
            b64 = base64.standard_b64encode(raw).decode("ascii")
            data_url = f"data:{content_type};base64,{b64}"

            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=512,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sos un asistente de compras. Se te enviará una imagen: "
                            "puede ser una lista de compras escrita a mano, una captura de pantalla "
                            "de un chat o nota, o cualquier imagen con productos a comprar. "
                            "Extraé todos los productos y cantidades que aparezcan y devolvé "
                            "un mensaje de texto listo para enviar a un chat de supermercado, "
                            "en español, de forma natural. "
                            "Ejemplo: 'necesito 2 litros de leche, pan lactal, 6 huevos y yogur de frutilla'. "
                            "Si no hay productos reconocibles, respondé solo: NO_LIST. "
                            "Devolvé únicamente el mensaje o NO_LIST, sin explicaciones ni formato adicional."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                        ],
                    },
                ],
            )
            text = (response.choices[0].message.content or "").strip()
            if text == "NO_LIST":
                self.send_json(envelope(error="No se encontraron productos en la imagen"), 422)
                return
            _log.info("describe-image: extracted %d chars", len(text))
            self.send_json(envelope(data={"text": text}))
        except Exception as e:
            _log.error("describe-image error: %s", e, exc_info=True)
            self.send_json(envelope(error=str(e)), 500)

    def _serve_static(self, path: str):
        if path == "/" or path == "":
            path = "/index.html"
        frontend_root = (ROOT / "frontend").resolve()
        file_path = (frontend_root / unquote(path.lstrip("/"))).resolve()
        try:
            file_path.relative_to(frontend_root)
        except ValueError:
            try:
                self.send_response(403)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if not file_path.exists() or not file_path.is_file():
            try:
                self.send_response(404)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        content_type = _guess_type(file_path.suffix)
        with open(file_path, "rb") as f:
            data = f.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _read_raw_body(self, max_bytes: int = 25_000_000) -> bytes | None:
        """Read raw binary request body up to max_bytes. Returns None on error or empty."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            return None
        if length <= 0 or length > max_bytes:
            _log.warning("raw body size %d out of accepted range (max %d)", length, max_bytes)
            return None
        return self.rfile.read(length)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            return {}
        if length <= 0:
            return {}
        if length > 1_000_000:  # 1 MB hard cap
            _log.warning("request body too large (%d bytes), rejecting", length)
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}


def _guess_type(suffix: str) -> str:
    return {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }.get(suffix.lower(), "application/octet-stream")
