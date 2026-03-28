"""
Route dispatch, request parsing, response envelope.
Owner: Nacho

All responses use the envelope:
    { "ok": bool, "data": any, "error": str|null, "request_id": str }
"""

import json
import os
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from backend.db import get_db

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


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
            result = handle_chat(
                message=body.get("message", ""),
                history=body.get("history", []),
                cart=body.get("cart", []),
                clarification_response=body.get("clarification_response"),
            )
            self.send_json(envelope(data=result))
        except Exception as e:
            self.send_json(envelope(error=str(e)), 500)

    def _handle_auth_register(self, body: dict):
        # TODO (Nacho): implement registration with SQLite
        self.send_json(envelope(error="Not implemented yet"), 501)

    def _handle_auth_login(self, body: dict):
        # TODO (Nacho): implement login with SQLite
        self.send_json(envelope(error="Not implemented yet"), 501)

    def _handle_orders_get(self):
        # TODO (Nacho): return order history from SQLite
        self.send_json(envelope(data={"orders": []}))

    def _handle_orders_post(self, body: dict):
        # TODO (Nacho): persist order to SQLite
        order_id = str(uuid.uuid4())[:8]
        cart = body.get("cart", [])
        total = sum(i.get("price", 0) * i.get("quantity", 1) for i in cart)
        self.send_json(envelope(data={"order_id": order_id, "total": round(total, 2)}))

    def _serve_static(self, path: str):
        if path == "/" or path == "":
            path = "/index.html"
        file_path = ROOT / "frontend" / path.lstrip("/")
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
