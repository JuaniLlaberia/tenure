# Stage 0: Setup

**Status:** done
**Depends on:** –
**Spec:** [brain-engine.md](../specs/brain-engine.md) §11 (module layout)

## Goal

Dependencies installed, the package skeleton in place, and the test setup ready, so stage 1 can start writing tests.

## Steps

1. Add dependencies (approved Oct 8):
   ```bash
   uv add langgraph openai httpx pyyaml python-dotenv
   ```
   This updates `pyproject.toml` and `uv.lock` together (CI runs `uv sync --locked`).
2. `.env.example` (approved Oct 8): fill in the brain's defaults and add the fallback model:
   ```
   MODEL_LEAD=deepseek/deepseek-v4-flash-0731
   MODEL_SPECIALIST=deepseek/deepseek-v4-flash-0731
   MODEL_REFLECT=deepseek/deepseek-v4-flash-0731
   MODEL_DECIDE_FALLBACK=deepseek/deepseek-v4-flash-0731
   MODEL_JEV=typesafe/jev-1.13
   ```
3. `pyproject.toml`, pytest section: register the `live` marker and skip it by default.
   ```toml
   markers = ["live: calls real OpenRouter models (needs OPENROUTER_API_KEY)"]
   addopts = "--import-mode=importlib -m 'not live'"
   ```
   `uv run pytest -m live` overrides the default `-m`.
4. Package skeleton (empty `__init__.py` files only; modules come with their stage):
   ```
   src/brain/graphs/__init__.py
   src/brain/flows/__init__.py
   src/brain/templates/__init__.py
   src/brain/prompts/__init__.py
   ```
5. `tests/brain/test_smoke.py`: one test that imports `brain`, `brain.graphs`, `brain.flows`, `langgraph`, `openai`, `httpx` and `yaml`. Delete `tests/brain/.gitkeep`.

There's no review step here: the only test is the smoke test.

## Done when

- [x] `uv sync` works from a clean checkout
- [x] `uv run ruff check .` passes
- [x] `uv run pytest` passes (contract tests + smoke test), and `uv run pytest -m live` collects nothing yet

## Log

- Oct 8: done. Added `httpx`, `python-dotenv`, `pyyaml` (`langgraph` and `openai` were already added by Juan). `.env.example` has the model defaults and `MODEL_DECIDE_FALLBACK`. `live` marker registered and skipped by default. Created `__init__.py` in `graphs/`, `flows/`, `templates/`, `prompts/`; smoke test in `tests/brain/test_smoke.py`; removed `tests/brain/.gitkeep`. `uv sync --locked`, `ruff check` and `pytest` (28 passed) pass; `pytest -m live` collects nothing.
- Left untouched (Juan's scaffolding): `src/brain/brain.py`, `src/brain/helpers/llm.py`, `src/brain/helpers/jev.py`, `src/brain/graphs/team.py`, `src/brain/graphs/specialist.py`. The layout question (docs' `facade.py`, `llm.py`, `decide.py` vs these) is open; settle it before stage 1.
