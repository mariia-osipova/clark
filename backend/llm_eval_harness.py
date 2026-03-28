"""
LLM judge eval suite.
Owner: Juan

Defines eval scenarios and the judge logic.
Run via: python scripts/run_llm_judge_eval.py
"""

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class Scenario:
    id: str
    description: str
    user_message: str
    cart_before: list[dict] = field(default_factory=list)
    expected_product_ids: list[str] = field(default_factory=list)
    expect_clarification: bool = False
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
        tags=["v1", "cart"],
    ),
    Scenario(
        id="v1_quantity",
        description="User asks for 2 yogures → cart has quantity 2",
        user_message="agrega 2 yogures",
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

def judge_response(
    scenario: Scenario,
    reply: str,
    cart: list[dict] | None,
    clarification: dict | None,
    openai_client: Any | None = None,
) -> EvalResult:
    """
    Evaluate a chat response against a scenario.
    V0: rule-based checks only.
    V2+: use LLM judge for semantic correctness.
    """
    # Rule: reply must not be empty
    if not reply or not reply.strip():
        return EvalResult(scenario.id, False, "Reply is empty", reply, cart or [])

    # Rule: if clarification expected, check it's present
    if scenario.expect_clarification:
        if not clarification:
            return EvalResult(scenario.id, False, "Expected clarification but got none", reply, cart or [])
        return EvalResult(scenario.id, True, "Clarification returned as expected", reply, cart or [])

    # Rule: expected product IDs must be in the cart
    if scenario.expected_product_ids and cart:
        cart_ids = {i.get("product_id") for i in cart}
        missing = set(scenario.expected_product_ids) - cart_ids
        if missing:
            return EvalResult(scenario.id, False, f"Missing products in cart: {missing}", reply, cart)

    return EvalResult(scenario.id, True, "Passed rule-based checks", reply, cart or [])
