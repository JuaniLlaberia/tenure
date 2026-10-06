# Tenure

> Working name. Innovation Intelligence Hackathon, UC Berkeley, SF Tech Week, Oct 5–11, 2026.

Solo founders hire AI teams for any function. Each team lives in the founder's Telegram, learns the business once, gets better with every piece of feedback, and earns more autonomy as the founder approves its work.

## Docs

- [docs/CONTEXT.md](docs/CONTEXT.md): what we're building and why
- [docs/CONTRACT.md](docs/CONTRACT.md): what every shared model and method means (exact shapes in `src/contract/`)
- [docs/specs/](docs/specs/): module specs
- [CLAUDE.md](CLAUDE.md): rules for AI coding assistants (and humans)

## Layout

| Folder | Owner | |
| --- | --- | --- |
| `src/contract/` | Both | Shared models and Protocols: `brain_side/`, `app_side/`, `models/` |
| `src/brain/` | Juan | Agents: LangGraph, prompts, team templates, learning, autonomy |
| `src/app/` | Mark | Telegram, FastAPI, Supabase, dashboard, integrations |
| `tests/` | Both | `contract/`, `brain/`, `app/` |

## Setup

Requires [uv](https://docs.astral.sh/uv/). It installs the pinned Python (3.12) for this project automatically.

```bash
uv sync                 # create .venv and install everything
cp .env.example .env    # then fill in the keys you need
uv run pytest           # run the tests
uv run ruff check . --fix   # lint; we don't use ruff format (see CLAUDE.md)
```

## Branches

`main` holds the shared setup. Work happens on `dev`, with one branch per side (brain and app) merged into `dev` at the merge points.

Every push and pull request to `main` or `dev` runs CI on GitHub (`.github/workflows/ci.yml`): `uv sync --locked`, `ruff check` and `pytest`. Run the same three locally before opening a pull request.
