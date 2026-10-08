# Stage 9: CLI, Postgres checkpointer, end to end

**Status:** not started
**Depends on:** stages 6, 7, 8
**Spec:** [brain-engine.md](../specs/brain-engine.md) §2; [CONTRACT.md](../CONTRACT.md) §10 (checkpoints), §12 (CLI)

## Goal

Run the whole brain from the terminal (`uv run python -m brain.cli`) against real models with fake Store and Tools, keep checkpoints in Supabase Postgres when `DATABASE_URL` is set, and prove the demo script works end to end.

## Before starting

Ask Juan to approve the dependencies: `langgraph-checkpoint-postgres` and `psycopg[binary]` (the Postgres saver needs psycopg 3).

## Files

| File | Contains |
| --- | --- |
| `src/brain/checkpoint.py` | `open_checkpointer()` |
| `src/brain/cli.py` | the terminal chat |
| `tests/brain/test_e2e.py` | the demo script on fakes |

## Interfaces

```python
# checkpoint.py
@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver]: ...
    # no DATABASE_URL → InMemorySaver
    # DATABASE_URL → AsyncPostgresSaver.from_conn_string(url), await saver.setup() once
```

The app uses it around `create_brain(..., checkpointer=saver)`. `DATABASE_URL` must be a direct or session-mode connection (CONTRACT §10).

### CLI

`uv run python -m brain.cli [--fake-models]`: `InMemoryStore`, `FakeTools(echo=True)`, real `OpenRouterLLM` / `OpenRouterJev` (or `FakeLLM` / `FakeJev` with `--fake-models`). One business per run.

| Command | Calls |
| --- | --- |
| `/start` | `start_onboarding` |
| `/hire <template>` | `hire_team`; switches to the new team |
| `/team <name>` / `/general` | switches the current topic |
| plain text | `handle_message` in the current topic |
| `/approve`, `/edit <text>`, `/reject [reason]` | `resolve_approval` on the latest pending approval |
| `/accept`, `/decline` | `respond_promotion` on the latest offer |
| `/undo` | `undo_action` on the latest undoable action |
| `/seed <task_type> <n>` | seeds `n` approved approvals at confidence 0.9 and streak `n` (demo only; we say so) |
| `/lessons`, `/tasks`, `/log` | prints what the Store holds |
| `/quit` | exits |

Events print as `Maya (Marketing lead): …`, with `[approval a1b2]`, `[undo until 14:32]` and similar tags.

## Tests

`tests/brain/test_checkpoint.py`

- `test_in_memory_without_database_url`
- `test_postgres_with_database_url` (marked `live`: needs a real database)

`tests/brain/test_e2e.py` (fakes only)

- `test_demo_script`:
  1. `start_onboarding` → answers → `OnboardingComplete(scope="business")`
  2. `hire_team("marketing")` → `TeamHired` → 3 onboarding answers → `OnboardingComplete(scope="team")`
  3. "We launch Friday, get the word out" → two `NeedsApproval` (post + newsletter)
  4. edit the post → `ActionDone` + `LessonLearned`
  5. approve the newsletter → `ActionDone` without an undo window
  6. seed the post streak to 4, ask for another post, approve → `PromotionOffer`; accept → level `act_and_report`
  7. one more post request → `ActionDone` with no approval
- `test_cli_commands_parse`: each command maps to the right `Brain` call (no network).

`tests/brain/test_live.py` (adds, marked `live`)

- `test_live_demo_script`: steps 2–4 against OpenRouter; asserts event types only.

## Done when

- [ ] All tests above pass (live ones skipped); earlier stages still pass
- [ ] `uv run python -m brain.cli` runs the demo by hand with real models
- [ ] `ruff check` passes

## Log

_Empty._
