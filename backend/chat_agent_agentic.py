"""
Agentic shopping flow — main agent logic.
Owner: Jeremias

Entry point: handle_chat()
"""

import json
import os
from pathlib import Path
from typing import Any

# Lazy import to avoid crashing if openai not installed at import time
def _openai():
    try:
        import openai
        return openai
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "catalog_snapshot.json"


def _load_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH) as f:
        return json.load(f)


def _build_system_prompt(catalog_summary: str) -> str:
    return f"""Eres un asistente de compras inteligente para supershop, un supermercado online.
Tu trabajo es ayudar al usuario a armar su carrito de compras de forma eficiente.

Catálogo disponible (resumen):
{catalog_summary}

Instrucciones:
- Respondé siempre en español, de forma amable y concisa.
- Cuando el usuario pida un producto, identificá el ítem más adecuado del catálogo.
- Devolvé siempre el carrito actualizado usando la herramienta set_cart.
- Si no encontrás un producto exacto, sugerí la alternativa más cercana.
- Nunca inventes precios ni productos que no estén en el catálogo.
- Si el usuario pregunta algo no relacionado con compras, redirigilo amablemente.
"""


def _catalog_summary(catalog: list[dict], max_items: int = 50) -> str:
    """Compact catalog representation for the system prompt."""
    lines = []
    for p in catalog[:max_items]:
        line = f"- [{p['id']}] {p['name']} ({p.get('brand','')}, {p.get('package_size','')}) ${p.get('price',0):.2f}"
        if p.get('discount_pct', 0) > 0:
            line += f" [{p['discount_pct']}% OFF]"
        lines.append(line)
    if len(catalog) > max_items:
        lines.append(f"... y {len(catalog) - max_items} productos más.")
    return "\n".join(lines)


# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_cart",
            "description": "Replace the entire cart with the given items. Call this after every cart mutation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {"type": "string"},
                                "name": {"type": "string"},
                                "brand": {"type": "string"},
                                "package_size": {"type": "string"},
                                "price": {"type": "number"},
                                "quantity": {"type": "integer"},
                                "image_url": {"type": "string"},
                            },
                            "required": ["product_id", "name", "price", "quantity"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_clarification",
            "description": "Ask the user to choose between ambiguous product options before proceeding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question to show the user"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "product": {"type": "object"},
                            },
                            "required": ["id", "label"],
                        },
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
]


# ─── Main entry point ─────────────────────────────────────────────────────────

def handle_chat(
    message: str,
    history: list[dict],
    cart: list[dict],
    clarification_response: dict | None = None,
) -> dict[str, Any]:
    """
    Process a chat turn and return:
        { "reply": str, "cart": list | None, "clarification": dict | None }
    """
    openai = _openai()
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    catalog = _load_catalog()
    system_prompt = _build_system_prompt(_catalog_summary(catalog))

    messages = [{"role": "system", "content": system_prompt}]

    # Inject current cart as context
    if cart:
        cart_text = "Carrito actual:\n" + "\n".join(
            f"- {i.get('name')} x{i.get('quantity')} ${i.get('price', 0):.2f}" for i in cart
        )
        messages.append({"role": "system", "content": cart_text})

    # Inject clarification context if this is a continuation
    if clarification_response:
        messages.append({
            "role": "system",
            "content": f"El usuario eligió la opción: {clarification_response.get('chosen_option_id')} "
                       f"para la solicitud pendiente: {clarification_response.get('pending_request_id')}",
        })

    messages.extend(history[-20:])
    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=1024,
    )

    result_cart = None
    clarification = None
    reply = ""

    choice = response.choices[0]

    # Handle tool calls
    if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            args = json.loads(tc.function.arguments)
            if tc.function.name == "set_cart":
                result_cart = _validate_cart(args.get("items", []), catalog)
                reply = choice.message.content or "Listo, actualicé tu carrito."
            elif tc.function.name == "request_clarification":
                import uuid
                clarification = {
                    "question": args.get("question", "¿Cuál preferís?"),
                    "options": args.get("options", []),
                    "pending_request_id": str(uuid.uuid4()),
                }
                reply = choice.message.content or args.get("question", "¿Cuál preferís?")
    else:
        reply = choice.message.content or ""

    return {"reply": reply, "cart": result_cart, "clarification": clarification}


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
