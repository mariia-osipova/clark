# supershop — Hackathon Repo

## Project
AI-powered shopping assistant. See [plan.md](plan.md) for full version roadmap.
Current target: **VERSION2** — complex queries, recipes, and stock-aware alternatives.

## Team identity
When a team member introduces themselves, read their file immediately before doing anything else:

| Introduction | File to read |
|---|---|
| "Im Jeremias" / "Soy Jeremias" | [team/JEREMIAS.md](team/JEREMIAS.md) |
| "Im Juan" / "Soy Juan" | [team/JUAN.md](team/JUAN.md) |
| "Im Mariia" / "Im Mari" / "Soy Mari" | [team/MARIIA.md](team/MARIIA.md) |
| "Im Nacho" / "Soy Nacho" | [team/NACHO.md](team/NACHO.md) |

Once you read their file, follow the instructions inside it for how to assist them.

## Repo layout
```
frontend/          Vanilla HTML/CSS/JS
backend/           Python 3 stdlib server + agent logic
data/              Catalog snapshots and semantic index
scripts/           Eval and tooling scripts
docs/              Architecture notes, API contracts, running log
team/              Per-person responsibility files
```

## Dev log
All significant decisions, blockers, and progress must be logged. See [docs/LOGGING.md](docs/LOGGING.md) for instructions. Append entries to [docs/LOG.md](docs/LOG.md).

## Non-negotiables
- Keep external stack stable: vanilla frontend, Python stdlib backend, SQLite, JSON snapshot/index, OpenAI-based agent via LangGraph.
- Prefer additive changes over rewrites.
- Do not start next version until current version has one reproducible happy-path demo.
- Before merging a version, run the unite gate checklist from [plan.md](plan.md).
