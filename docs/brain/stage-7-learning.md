# Stage 7: Learning

**Status:** not started
**Depends on:** stage 6
**Spec:** [brain-engine.md](../specs/brain-engine.md) §7 (learning loop); [CONTRACT.md](../CONTRACT.md) §6 (`LessonLearned`), §11 (`Lesson`)

## Goal

Feedback becomes lessons, and lessons change the next draft. REFLECT turns an edit, a rejection or a chat message into 0–2 lessons, replaces the ones they repeat or contradict, and the lead confirms each with `LessonLearned`. Lessons already reach the specialist and check prompts through `build_context` (stage 5) and `run_check` (stage 4); this stage checks it end to end.

## Files

| File | Contains |
| --- | --- |
| `src/brain/learning.py` | `LessonDraft`, `ReflectOutput`, `reflect()`, `learn()` |
| `src/brain/prompts/reflect.py` | the reflect messages |
| `src/brain/graphs/team.py` | `triage` calls `learn` when `has_feedback` is accepted |
| `src/brain/brain.py` | wires `learn` into the team graph and into `resolve_approval` |

## Interfaces

```python
# learning.py
class LessonDraft(BaseModel):
    text: str                             # short, imperative
    kind: Literal["fact", "preference"]
    business_wide: bool
    task_type: str | None = None
    replaces: str | None = None           # lesson_id of an existing lesson it repeats or contradicts

class ReflectOutput(BaseModel):
    lessons: list[LessonDraft]            # 0–2

async def reflect(
    deps: Deps, template: Template, feedback: str, existing: list[Lesson]
) -> tuple[list[LessonDraft], int]: ...  # (drafts, tokens)

async def learn(
    deps: Deps, *, business_id: str, team_id: str, template: Template,
    source: Literal["edit", "reject", "chat"], source_ref: str | None, feedback: str,
    task_type: str | None = None,
) -> AsyncIterator[Event]: ...           # saves lessons, yields LessonLearned
```

### `learn()` steps

1. `existing = store.list_lessons(business_id, team_id)`.
2. `reflect()` on `model_reflect`. The prompt has the feedback, the existing lessons with their ids, the team's task types (with `task_type`, the approval's, as a hint), and the rule: business-wide only for facts or rules about the business itself ("we never offer discounts"), team scope for how this team's work should look ("no hashtags").
3. Clean up the drafts in code: keep at most 2; drop empty text; unknown `task_type` → `None`; `replaces` not an id in `existing` → ignored.
4. For each draft: deactivate the replaced lesson (`active=False`, saved), save a new `Lesson` (`team_id=None` if business-wide, `source`, `source_ref`, `created_at = clock()`), yield `LessonLearned(lesson_id, team_id, persona=lead, text, business_wide)`.
5. `reflect()` fails → no lessons, no events, no exception (learning never blocks the work).

### Wiring

| Signal | Where | `source` / `source_ref` | Feedback text |
| --- | --- | --- | --- |
| Chat | team graph `triage`, when `has_feedback` is accepted | `chat` / `message_id` | the message |
| Edit | `resolve_approval` `learn` hook | `edit` / `approval_id` | `"Original:\n…\n\nEdited:\n…"` |
| Reject with reason | `resolve_approval` `learn` hook | `reject` / `approval_id` | `"Draft:\n…\n\nRejected because: …"` |

A message that is both feedback and work ("stop using hashtags, and post about Friday") yields `LessonLearned` first, then the work runs with the new lesson already in the context.

## Tests

`tests/brain/test_learning.py`

- `test_reflect_returns_drafts`: scripted `ReflectOutput` comes back; prompt contains the feedback and the existing lessons' ids.
- `test_learn_saves_team_lesson_and_yields_event`: `team_id` set, `business_wide=False`.
- `test_learn_saves_business_wide_lesson`: `team_id=None`, `business_wide=True`, and another team of the business sees it in `list_lessons`.
- `test_learn_replaces_existing_lesson`: the old one is inactive; only the new one is listed.
- `test_learn_ignores_unknown_replaces_and_task_type`
- `test_learn_keeps_at_most_two`
- `test_learn_survives_reflect_failure`: `FakeLLM(fail=True)` → no events, no exception.

`tests/brain/test_learning_wired.py` (through the facade)

- `test_chat_feedback_becomes_lesson`: "Stop using hashtags" with `has_feedback` yes and no task type routed → `LessonLearned` with `source="chat"` and `source_ref=message_id`, then one `Say`, no tasks.
- `test_feedback_and_work_in_one_message`: `LessonLearned` before `NeedsApproval`; the writer's prompt contains the new lesson.
- `test_no_feedback_no_reflect_call`: `has_feedback` no → no `ReflectOutput` call.
- `test_edit_becomes_lesson`: `source="edit"`, `source_ref=approval_id`; reflect saw original and edited text.
- `test_reject_with_reason_learns_then_revises`: `LessonLearned`, then a new `NeedsApproval`; the revised writer prompt contains the new lesson and the reason.
- `test_lessons_reach_the_check`: an active lesson appears in the `passes_check` state sent to Jev.
- `test_approved_drafts_become_examples`: after an approve, the next `social_post` writer prompt contains it as an example.

## Done when

- [ ] All tests above pass; earlier stages still pass
- [ ] `ruff check` passes

## Log

_Empty._
