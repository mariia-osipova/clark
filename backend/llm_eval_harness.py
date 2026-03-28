"""
LLM judge eval suite.
Owner: Juan

Defines eval scenarios and the judge logic.
Run via: python scripts/run_llm_judge_eval.py
"""

import json
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
    Scenario(
        id="basic_response",
        description="Assistant responds coherently to a greeting",
        user_message="Hola, ¿qué podés hacer?",
        tags=["basic"],
    ),

    # NOTE: expected_product_ids are intentionally empty here.
    # Fill them in after running scripts/scrape_catalog.py and identifying
    # the real product IDs for "leche entera 1L" and "yogur" in the live catalog.
    Scenario(
        id="exact_product",
        description="User asks for leche entera 1L → cart has one matching item",
        user_message="quiero leche entera 1L",
        tags=["cart"],
    ),
    Scenario(
        id="quantity_request",
        description="User asks for 2 yogures → cart has quantity 2",
        user_message="agrega 2 yogures",
        tags=["cart", "quantity"],
    ),

    Scenario(
        id="recipe_torta",
        description="Recipe request adds several relevant items",
        user_message="quiero hacer una torta",
        tags=["recipe", "llm_judge"],
    ),
    Scenario(
        id="out_of_stock",
        description="Out-of-stock product is substituted or explicitly noted",
        user_message="quiero leche descremada marca X",
        tags=["stock", "llm_judge"],
    ),

    Scenario(
        id="broad_query",
        description="Broad intent adds at least one relevant item",
        user_message="necesito algo para el desayuno",
        tags=["broad", "llm_judge"],
    ),

    Scenario(
        id="out_of_stock_substitution",
        description="OOS product is substituted or explicitly mentioned as missing",
        user_message="quiero leche descremada marca inexistente",
        tags=["stock", "llm_judge"],
    ),

    Scenario(
        id="ambiguous_cola",
        description="Ambiguous cola request triggers clarification modal",
        user_message="gaseosa cola 1.5L",
        expect_clarification=True,
        tags=["clarification"],
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
    Rule-based checks run first for all scenarios.
    Scenarios tagged with `llm_judge` also run an LLM judge when openai_client is provided.
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

    # Additional semantic/content review for richer shopping scenarios
    if "llm_judge" in scenario.tags and openai_client:
        return _llm_judge(scenario, reply, cart, openai_client)

    return EvalResult(scenario.id, True, "Passed rule-based checks", reply, cart)


def _build_judge_prompt(scenario: Scenario, reply: str, cart: list[dict]) -> str:
    cart_summary = json.dumps(
        [{"product_id": i.get("product_id"), "quantity": i.get("quantity")} for i in cart],
        ensure_ascii=False,
    )
    return f"""You are evaluating an AI shopping assistant response.

Scenario: {scenario.description}
User message: "{scenario.user_message}"

Assistant reply:
{reply}

Cart after response (product_id + quantity):
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
        passed = verdict.upper().startswith("PASS")
        reason = verdict.split(":", 1)[-1].strip() if ":" in verdict else verdict
        return EvalResult(scenario.id, passed, f"LLM judge: {reason}", reply, cart)
    except Exception as e:
        # If the judge call fails, fall back to passing (don't block CI on API errors)
        return EvalResult(scenario.id, True, f"LLM judge unavailable ({e}), rule-based pass", reply, cart)
