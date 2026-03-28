"""
Entry point — ThreadingHTTPServer
Owner: Nacho

Usage:
    python backend/server.py

Environment variables (see .env.example):
    PORT        default 8000
    DB_PATH     default data/proxy_store.db
    OPENAI_API_KEY  required
"""

import os
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

# Ensure repo root is on the path so backend imports work
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import RequestHandler
from backend.db import init_db


def load_env(path: str = ".env") -> None:
    """Minimal .env loader — no external deps required."""
    env_file = ROOT / path
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_env()
    init_db()

    try:
        port = int(os.environ.get("PORT", 8000))
    except ValueError:
        print("WARNING: invalid PORT value, using 8000")
        port = 8000
    host = os.environ.get("HOST", "0.0.0.0")

    if not os.environ.get("OPENAI_API_KEY"):
        print("WARNING: OPENAI_API_KEY is not set. Chat endpoint will not work.")

    # Pre-warm the sentence-transformer model so the first chat request isn't slow
    try:
        from backend.product_semantic_index import _load_model
        print("Loading embedding model...", end=" ", flush=True)
        _load_model()
        print("ready.")
    except Exception as e:
        print(f"WARNING: could not pre-load embedding model: {e}")

    server = ThreadingHTTPServer((host, port), RequestHandler)
    print(f"supershop server running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
