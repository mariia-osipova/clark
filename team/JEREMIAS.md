# Jeremias — Agent & Prompt Strategy

You are assisting Jeremias. His focus is the AI agent behavior, prompt strategy, multi-step cart assembly, clarification logic, and automation logic.

## Files he owns
- `backend/chat_agent.py` — thin compatibility layer
- `backend/chat_agent_agentic.py` — main agentic shopping flow
- Any prompt templates or system message definitions

## Current version: VERSION3 active

## Current version: VERSION3 ✅ → VERSION4 upcoming

## Current focus (V3 complete)
- [x] Tighten clarification generation and continuation logic in `backend/chat_agent_agentic.py`
- [x] Make ambiguous requests produce structured option sets instead of free-form uncertainty
- [x] Balance user intent with offer-aware ranking without making overly aggressive substitutions

## V4 focus
- [ ] Swap `search_products` tool for `resolve_product` + `add_to_cart` in the agent graph once Juan and Nacho deliver those tools. Agent calls `resolve_product()` and routes on verdict, never decides product ambiguity itself.
- [ ] Simplify system prompt to ~4 routing rules: if `resolved` → `add_to_cart`; if `needs_clarification` → `request_clarification`; if `not_found` → report missing.
- [ ] Add monthly basket graph node: calls `POST /api/v1/recurring-plan/generate`, presents result to user, handles overrides.
- [ ] Make recurring generation reproducible enough that the team can review and trust the output live.

## How to help Jeremias
- When he describes a behavior or prompt change, implement it in `chat_agent_agentic.py`.
- Keep agent logic self-contained and testable — avoid side effects outside of explicit cart mutations.
- When adding a new agent tool or prompt change, log it in [docs/LOG.md](../docs/LOG.md).
- Cross-reference [docs/api.md](../docs/api.md) for the chat endpoint contract before changing payloads.

## Key conventions
- The agentic flow lives in `chat_agent_agentic.py`. `chat_agent.py` is only a compatibility shim.
- Do not patch agent behavior with ad hoc phrase rules or case-by-case guards. If behavior must be enforced, enforce it through LangGraph nodes, edges, state transitions, or tool-grounded runtime invariants.
- Cart mutations must be validated server-side before returning to the UI.
- Reply tone: helpful, concise, Spanish-aware (users may write in Spanish).
