# Jeremias — Agent & Prompt Strategy

You are assisting Jeremias. His focus is the AI agent behavior, prompt strategy, multi-step cart assembly, clarification logic, and automation logic.

## Files he owns
- `backend/chat_agent.py` — thin compatibility layer
- `backend/chat_agent_agentic.py` — main agentic shopping flow
- Any prompt templates or system message definitions

## Current version: VERSION0 tasks
- [ ] Wrap a simple chat backend around the OpenAI API
- [ ] Define the initial prompt style and reply tone
- [ ] Make the assistant aware of the catalog context at a high level

## Version roadmap (Jeremias)
| Version | Focus |
|---|---|
| V0 | Basic chat wrapper, prompt style, catalog context awareness |
| V1 | First real add-to-cart loop, agentic path, strict cart-setting tools |
| V2 | Query decomposition for recipe/goal prompts, reply summaries |
| V3 | Clarification generation and continuation logic, structured option sets |
| V4 | Monthly planning prompt, automation logic, recurring generation |

## How to help Jeremias
- When he describes a behavior or prompt change, implement it in `chat_agent_agentic.py`.
- Keep agent logic self-contained and testable — avoid side effects outside of explicit cart mutations.
- When adding a new agent tool or prompt change, log it in [docs/LOG.md](../docs/LOG.md).
- Cross-reference [docs/api.md](../docs/api.md) for the chat endpoint contract before changing payloads.

## Key conventions
- The agentic flow lives in `chat_agent_agentic.py`. `chat_agent.py` is only a compatibility shim.
- Cart mutations must be validated server-side before returning to the UI.
- Reply tone: helpful, concise, Spanish-aware (users may write in Spanish).
