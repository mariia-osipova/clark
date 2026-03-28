"""
Run the LLM judge eval suite.
Owner: Juan

Usage:
    python scripts/run_llm_judge_eval.py [--tags v1,v2] [--scenario-id v1_exact_product]

Outputs a summary table and exits with code 1 if any scenario fails.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.llm_eval_harness import SCENARIOS, judge_response
from backend.chat_agent_agentic import handle_chat


def run_scenarios(scenarios, verbose=False):
    results = []
    for s in scenarios:
        try:
            output = handle_chat(
                message=s.user_message,
                history=[],
                cart=s.cart_before,
            )
            result = judge_response(
                scenario=s,
                reply=output.get("reply", ""),
                cart=output.get("cart"),
                clarification=output.get("clarification"),
            )
        except Exception as e:
            from backend.llm_eval_harness import EvalResult
            result = EvalResult(s.id, False, f"Exception: {e}")

        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.scenario_id}: {result.reason}")
        if verbose and not result.passed:
            print(f"       Reply: {result.actual_reply[:120]}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{passed}/{total} scenarios passed.")
    return passed == total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tags", help="Comma-separated tag filter, e.g. v1,cart")
    parser.add_argument("--scenario-id", help="Run a single scenario by ID")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.scenario_id:
        scenarios = [s for s in scenarios if s.id == args.scenario_id]
    elif args.tags:
        tags = set(args.tags.split(","))
        scenarios = [s for s in scenarios if tags & set(s.tags)]

    if not scenarios:
        print("No matching scenarios found.")
        sys.exit(0)

    ok = run_scenarios(scenarios, verbose=args.verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
