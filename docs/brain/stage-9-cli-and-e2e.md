# Stage 9: CLI, Postgres checkpointer, end to end

**Status:** done; tests waiting for Juan's review
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

- [x] All tests above pass (live ones skipped); earlier stages still pass
- [ ] `uv run python -m brain.cli` runs the demo by hand with real models (Juan, by hand; the CLI's commands were smoke-tested with piped input, and the same demo path passes as `test_live_demo_script`)
- [x] `ruff check` passes
- [ ] Juan has reviewed the tests
- [ ] `test_postgres_with_database_url` run once against Supabase (needs `DATABASE_URL` in `.env`)

## Log

- Oct 9: implemented under `/goal start implementation for stage 9`. Dependencies added with Juan's OK: `langgraph-checkpoint-postgres` 3.1.2 and `psycopg[binary]` 3.3.6. Tests written first and run red (19 failed, 7 errors on stubs), then implemented: 309 passed.
- `brain/checkpoint.py` `open_checkpointer(settings)`: `InMemorySaver` without `DATABASE_URL`, else `AsyncPostgresSaver.from_conn_string(...)` plus `setup()`. The live Postgres test is a real round trip (write a checkpoint, reopen, read it back); it skips while `DATABASE_URL` is unset, as it is now.
- `brain/cli.py`: `parse()`, `seed_approvals()` (demo only; reuses the latest real draft so examples stay sensible) and `Cli` (`handle()`, `render()`). It remembers the latest approval, offer and undoable action. Each run gets a fresh `business_id`, so Postgres checkpoints from earlier runs never collide. `--fake-models` was dropped: without scripted answers fake models can't run a conversation, so the CLI needs `OPENROUTER_API_KEY`.
- Tests added beyond the list above: CLI routing tests (`test_plain_text_goes_to_the_current_topic`, `test_start_and_hire_call_the_brain`, `test_hired_team_becomes_the_current_topic`, `test_approval_commands_use_the_latest_approval`, `test_promotion_and_undo_use_the_latest_offer_and_action`, `test_commands_without_a_target_say_so`, `test_render_shows_persona_and_text`), `test_seed_approvals_sets_streak_and_history`, and `tests/brain/test_live_demo.py` (a new file, so the approved `test_live.py` stays untouched).
- `test_demo_script` moves the clock between demo steps: with equal timestamps, the edited post from step 4 could land among the "last 5" approvals and block the promotion.
- **The first live demo run failed:** the post request ended in an `Error` after 4.5 minutes. Two causes, both measured:
  1. The writer was never told the 300-character limit, so posts failed the hard check and, after 2 revisions, couldn't become a `PostSocial`. Fix: `OutputType.guidance` per output type, included in the specialist prompt (tests: `tests/brain/test_output_guidance.py`, 6).
  2. DeepSeek reasons by default: 63–1,257 reasoning tokens on a short post, 5–27 s a call. One call also hung for minutes (no timeout). Fix: `LLM_REASONING` (default `true`, the model's own behaviour; `false` sends `reasoning: {enabled: false}`) and `LLM_TIMEOUT` (60 s) (tests: `tests/brain/test_llm_options.py`, 4). Juan's call: keep reasoning on by default for co-worker quality, and turn it off for testing and the demo.
- After the fixes: the same post request took 56 s with reasoning off (149 s before), and `LLM_REASONING=false uv run pytest -m live tests/brain/test_live_demo.py` passed in 42 s.
- Different models per role need no code: `MODEL_LEAD`, `MODEL_SPECIALIST`, `MODEL_REFLECT`, `MODEL_DECIDE_FALLBACK`.
