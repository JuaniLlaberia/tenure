# Building the brain

Owner: Juan. The plan for building [the brain engine spec](../specs/brain-engine.md), one file per stage. Each stage is a slice that can be tested on its own, and each file has everything needed to pick the work up at that stage.

## How to resume

1. Check the status table below and open the first stage that isn't `done`.
2. Read its **Status** and **Log** sections: they say exactly where the stage stopped.
3. Read the spec sections it lists under **Spec**.
4. Continue from the workflow step it is at.

When a step finishes, update the stage file's status and log, and the table below.

## Workflow (test-driven)

For every stage:

1. **Tests first.** Write the stage's tests (listed in its file) in `tests/brain/`, plus the minimum stubs so they import: the signatures from **Interfaces**, with `raise NotImplementedError`.
2. **Red.** Run `uv run pytest`; the new tests fail.
3. **Review.** Juan reviews the tests in chat and approves them. Status → `tests approved`.
4. **Green.** Implement until every test passes. Lint with `uv run ruff check . --fix`.
5. **Done.** Status → `done`.

**Approved tests are frozen.** They don't change without Juan's OK. If a test looks wrong while implementing, stop and ask.

**Nothing is committed** until Juan says so.

### Rules for tests

- No network. LLM and Jev calls go through `FakeLLM` and `FakeJev` (stage 2), which return scripted answers. Tests check wiring and rules, not model quality.
- Real-model checks are marked `@pytest.mark.live` and skipped by default (`uv run pytest -m live` runs them; they need `OPENROUTER_API_KEY`).
- Store and Tools are `InMemoryStore` and `FakeTools` (stage 1), which also back the CLI.
- Time comes from `Deps.clock`, so tests use a fixed clock instead of sleeping.
- Shared fixtures live in `tests/brain/conftest.py`.

## Status

| # | Stage | Depends on | Status |
| --- | --- | --- | --- |
| 0 | [Setup](./stage-0-setup.md) | – | done |
| 1 | [Fakes and templates](./stage-1-fakes-and-templates.md) | 0 | done |
| 2 | [LLM layer and `decide()`](./stage-2-llm-and-decide.md) | 0 | done |
| 3 | [Autonomy and flows](./stage-3-autonomy-and-flows.md) | 1, 2 | done |
| 4 | [Check](./stage-4-check.md) | 1, 2 | done |
| 5 | [Specialist subgraph](./stage-5-specialist.md) | 1, 2 | done |
| 6 | [Team graph and facade (demo slice)](./stage-6-team-graph.md) | 3, 4, 5 | done |
| 7 | [Learning](./stage-7-learning.md) | 6 | done |
| 8 | [Company graph (business onboarding)](./stage-8-company-graph.md) | 6 | done |
| 9 | [CLI, Postgres checkpointer, end to end](./stage-9-cli-and-e2e.md) | 6–8 | done |
| 10 | [Voice notes, photos and files in](./stage-10-attachments.md) | 9 | done |
| 11 | [Design team and image generation](./stage-11-design-team.md) | 10 | done |
| 12 | [Marketing and design on one campaign](./stage-12-campaign-visuals.md) | 11 | done |
| 13 | [Avatars](./stage-13-avatars.md) | 11 | done |
| 14 | [Schedules](./stage-14-schedules.md) | 9 | done |
| 15 | [Images are made once](./stage-15-images-once.md) | 11, 12 | built by Mark, reviewed by Juan (Oct 9) |
| 16 | [Resilience](./stage-16-resilience.md) | 8–15 | built by Mark's session, needs Juan's review |

Statuses: `not started` → `tests written` (red, waiting for review) → `tests approved` → `done`.

Stages 3, 4 and 5 are independent of each other once 1 and 2 are done.

**Stages 10–14 (contract v0.6, Oct 9)** come from the feature plan in [ROADMAP.md](../ROADMAP.md), which has the order, the time box and what the app builds alongside each stage. Build them in order 10 → 11 → 12, then 13 and 14; 14 doesn't depend on 10–13 and can move up if images slip. The API formats, model candidates and prices in each stage file were checked on OpenRouter on Oct 9, so there's no need to research them again; only the live tests each stage lists. Until the app side lands, develop against `FakeTools` and `InMemoryStore` (extended in stages 10 and 14) and the CLI.

## Decisions so far (Oct 8)

- Dependencies approved: `langgraph`, `openai`, `httpx`, `pyyaml`, `python-dotenv`; at stage 9, `langgraph-checkpoint-postgres` and `psycopg[binary]`.
- Reasoning stays on by default (`LLM_REASONING=true`); set it to `false` for testing and the demo (Oct 9).
- `.env.example` may be edited for the brain's env vars (`MODEL_DECIDE_FALLBACK`, default model ids).
- Models: `deepseek/deepseek-v4-flash-0731` for every LLM role; Jev is `typesafe/jev-1.13` on OpenRouter's Decisions API (spec §9).
- Tests are reviewed per stage, in chat. Juan approved the tests of stages 1–4 on Oct 8, reviewing them as they were written, and of stages 5–9 on Oct 9. All tests are now frozen.
- Commits go to the `brain` branch (Juan OK'd committing on Oct 8). Push only when asked.
- Contract v0.4, v0.5 and v0.6 agreed by Juan (Oct 9).
- New settings for stages 10–11: `MODEL_MEDIA` (default `google/gemini-3.5-flash-lite`) and `MODEL_IMAGE` (default `google/gemini-3.1-flash-image`; candidates in stage 11). No new brain dependencies: the Images API is plain `httpx` (Oct 9).
- Stages 10–14 reviewed by Juan (Oct 9). Work happens on the `brain-v06` branch, committed per stage. Live tests are written but run only once the app and brain are connected. Each stage builds only what its own tests need. Changes to Mark's drafts: stage 12 keeps "suggest Design once" in team state, not as a lesson; stage 14 reads a stated timezone from the request.
