"""
LLM judge eval suite.
Owner: Juan

Defines eval scenarios and the judge logic.
Run via: python scripts/run_llm_judge_eval.py
"""

import json
import re
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Scenario:
    id: str
    description: str
    user_message: str
    cart_before: list[dict] = field(default_factory=list)
    expected_product_ids: list[str] = field(default_factory=list)
    expect_clarification: bool = False
    min_cart_size: int = 0           # cart must have at least this many items
    expected_min_quantity: int = 0   # at least one cart item must have quantity >= this
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    scenario_id: str
    passed: bool
    reason: str
    actual_reply: str = ""
    actual_cart: list[dict] = field(default_factory=list)


# ─── Scenario registry ───────────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [
    # V0 — basic chat
    Scenario(
        id="v0_basic_response",
        description="Assistant responds coherently to a greeting",
        user_message="Hola, ¿qué podés hacer?",
        tags=["v0", "basic"],
    ),

    # V1 — exact product match
    Scenario(
        id="v1_exact_product",
        description="User asks for leche entera 1L → cart has one matching item",
        user_message="quiero leche entera 1L",
        min_cart_size=1,
        tags=["v1", "cart"],
    ),
    Scenario(
        id="v1_quantity",
        description="User asks for 2 yogures → cart has quantity 2",
        user_message="agrega 2 yogures",
        min_cart_size=1,
        expected_min_quantity=2,
        tags=["v1", "cart", "quantity"],
    ),

    # V2 — recipe decomposition
    Scenario(
        id="v2_recipe_torta",
        description="Recipe request adds several relevant items",
        user_message="quiero hacer una torta",
        tags=["v2", "recipe"],
    ),
    Scenario(
        id="v2_out_of_stock",
        description="Out-of-stock product is substituted or explicitly noted",
        user_message="quiero leche descremada marca X",
        tags=["v2", "stock"],
    ),

    # V2 — broad query (semantic search required)
    Scenario(
        id="v2_broad_query",
        description="Broad intent adds at least one relevant item",
        user_message="necesito algo para el desayuno",
        tags=["v2", "broad"],
    ),

    # V2 — out-of-stock substitution (reply must mention the situation)
    Scenario(
        id="v2_out_of_stock_substitution",
        description="OOS product is substituted or explicitly mentioned as missing",
        user_message="quiero leche descremada marca inexistente",
        tags=["v2", "stock"],
    ),

    # V3 — clarification
    Scenario(
        id="v3_ambiguous_cola",
        description="Ambiguous cola request triggers clarification modal",
        user_message="gaseosa cola 1.5L",
        expect_clarification=True,
        tags=["v3", "clarification"],
    ),
]


# ─── Judge logic ─────────────────────────────────────────────────────────────

_VERDICT_RE = re.compile(r"^(PASS|FAIL):\s*(.+)", re.IGNORECASE | re.DOTALL)


def judge_response(
    scenario: Scenario,
    reply: str,
    cart: list[dict] | None,
    clarification: dict | None,
    openai_client: Any | None = None,
) -> EvalResult:
    """
    Evaluate a chat response against a scenario.
    Rule-based checks run first for all scenarios.
    V2-tagged scenarios also run an LLM judge when openai_client is provided.
    """
    cart = cart or []

    # Rule: reply must not be empty
    if not reply or not reply.strip():
        return EvalResult(scenario.id, False, "Reply is empty", reply, cart)

    # Rule: if clarification expected, check it's present
    if scenario.expect_clarification:
        if not clarification:
            return EvalResult(scenario.id, False, "Expected clarification but got none", reply, cart)
        return EvalResult(scenario.id, True, "Clarification returned as expected", reply, cart)

    # Rule: expected product IDs must be in the cart
    if scenario.expected_product_ids:
        cart_ids = {i.get("product_id") for i in cart}
        missing = set(scenario.expected_product_ids) - cart_ids
        if missing:
            return EvalResult(scenario.id, False, f"Missing products in cart: {missing}", reply, cart)

    # Rule: cart must meet minimum size
    if scenario.min_cart_size > 0 and len(cart) < scenario.min_cart_size:
        return EvalResult(
            scenario.id, False,
            f"Cart has {len(cart)} item(s), expected >= {scenario.min_cart_size}",
            reply, cart,
        )

    # Rule: at least one cart item must meet minimum quantity
    if scenario.expected_min_quantity > 0:
        max_qty = max((i.get("quantity", 0) for i in cart), default=0)
        if max_qty < scenario.expected_min_quantity:
            return EvalResult(
                scenario.id, False,
                f"No cart item has quantity >= {scenario.expected_min_quantity} (max found: {max_qty})",
                reply, cart,
            )

    # V2: LLM semantic judge
    if "v2" in scenario.tags and openai_client:
        return _llm_judge(scenario, reply, cart, openai_client)

    return EvalResult(scenario.id, True, "Passed rule-based checks", reply, cart)


def _build_judge_prompt(scenario: Scenario, reply: str, cart: list[dict]) -> str:
    cart_summary = json.dumps(
        [
            {
                "product_id": i.get("product_id"),
                "name": i.get("name", ""),
                "category": i.get("category", ""),
                "quantity": i.get("quantity"),
            }
            for i in cart
        ],
        ensure_ascii=False,
    )
    return f"""You are evaluating an AI shopping assistant response.

Scenario: {scenario.description}
User message: "{scenario.user_message}"

Assistant reply:
{reply}

Cart after response (product_id, name, category, quantity):
{cart_summary}

Judge whether the assistant handled the scenario correctly. Answer with exactly:
PASS: <one-line reason>
or
FAIL: <one-line reason>
"""


def _llm_judge(
    scenario: Scenario,
    reply: str,
    cart: list[dict],
    openai_client: Any,
) -> EvalResult:
    prompt = _build_judge_prompt(scenario, reply, cart)
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=100,
        )
        verdict = response.choices[0].message.content.strip()
        m = _VERDICT_RE.match(verdict)
        if not m:
            return EvalResult(
                scenario.id, False,
                f"LLM judge returned unrecognised format: {verdict[:80]}",
                reply, cart,
            )
        passed = m.group(1).upper() == "PASS"
        reason = m.group(2).strip()
        return EvalResult(scenario.id, passed, f"LLM judge: {reason}", reply, cart)
    except Exception as e:
        return EvalResult(scenario.id, False, f"LLM judge error: {e}", reply, cart)
