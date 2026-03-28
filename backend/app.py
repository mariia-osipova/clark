"""
Route dispatch, request parsing, response envelope.
Owner: Nacho

All responses use the envelope:
    { "ok": bool, "data": any, "error": str|null, "request_id": str }
"""

import json
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from backend.clarification_store import ClarificationStateError
from backend.db import get_db

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


def _load_catalog() -> list:
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH) as f:
        return json.load(f)


def _validate_order_cart(cart: list, catalog: list) -> list:
    """Validate cart items against catalog: drop unknowns and out-of-stock, enforce catalog prices."""
    catalog_map = {p["id"]: p for p in catalog}
    validated = []
    for item in cart:
        pid = item.get("product_id")
        product = catalog_map.get(pid)
        if not product:
            continue
        if product.get("available_quantity", 0) == 0:
            continue
        qty = max(1, int(item.get("quantity", 1)))
        validated.append({
            "product_id": pid,
            "name": product.get("name", ""),
            "brand": product.get("brand", ""),
            "package_size": product.get("package_size", ""),
            "price": product.get("price", 0.0),
            "quantity": qty,
            "image_url": product.get("image_url", ""),
        })
    return validated


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


def envelope(data=None, error=None, request_id=None):
    return {
        "ok": error is None,
        "data": data or {},
        "error": error,
        "request_id": request_id or str(uuid.uuid4()),
    }


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default access log noise; add structured logging here if needed
        pass

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/v1/catalog":
            self._handle_catalog()
        elif path == "/api/v1/orders":
            self._handle_orders_get()
        elif path == "/api/v1/preferences":
            self._handle_preferences_get()
        elif path.startswith("/"):
            # Serve static frontend files
            self._serve_static(path)
        else:
            self.send_json(envelope(error="Not found"), 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
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
            self.send_json(envelope(error=str(e)), 500)

    def _handle_chat(self, body: dict):
        try:
            from backend.chat_agent_agentic import handle_chat
            session_token = self.headers.get("X-Session-Token")
            if body.get("clarification_response") and not session_token:
                raise ClarificationStateError(
                    "Falta X-Session-Token para resolver la aclaración. Probá de nuevo."
                )
            result = handle_chat(
                message=body.get("message", ""),
                history=body.get("history", []),
                cart=body.get("cart", []),
                clarification_response=body.get("clarification_response"),
                session_token=session_token,
                context=_assemble_chat_context(),
            )
            self.send_json(envelope(data=result))
        except ClarificationStateError as e:
            self.send_json(envelope(error=str(e)), 400)
        except Exception as e:
            self.send_json(envelope(error=str(e)), 500)

    def _handle_auth_register(self, body: dict):
        # TODO (Nacho): implement registration with SQLite
        self.send_json(envelope(error="Not implemented yet"), 501)

    def _handle_auth_login(self, body: dict):
        # TODO (Nacho): implement login with SQLite
        self.send_json(envelope(error="Not implemented yet"), 501)

    def _handle_orders_get(self):
        try:
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT id, cart_json, total, created_at FROM orders ORDER BY created_at DESC"
                ).fetchall()
            finally:
                conn.close()
            orders = [
                {
                    "id": r["id"],
                    "items": json.loads(r["cart_json"]),
                    "total": r["total"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            self.send_json(envelope(data={"orders": orders}))
        except Exception as e:
            self.send_json(envelope(error=str(e)), 500)

    def _handle_orders_post(self, body: dict):
        try:
            catalog = _load_catalog()
            cart = body.get("cart", [])
            validated = _validate_order_cart(cart, catalog)
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
            self.send_json(envelope(data={"order_id": order_id, "total": total}))
        except Exception as e:
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

    def _serve_static(self, path: str):
        if path == "/" or path == "":
            path = "/index.html"
        frontend_root = (ROOT / "frontend").resolve()
        file_path = (frontend_root / path.lstrip("/")).resolve()
        try:
            file_path.relative_to(frontend_root)
        except ValueError:
            self.send_response(403)
            self.end_headers()
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        content_type = _guess_type(file_path.suffix)
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
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
