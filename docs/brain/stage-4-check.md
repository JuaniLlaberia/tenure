# Stage 4: Check

**Status:** done
**Depends on:** stages 1, 2
**Spec:** [brain-engine.md](../specs/brain-engine.md) §5 (check), §9 (`decide()` pattern)

## Goal

The independent review of a finished draft: hard validators in code, then a pass / revise judgement through `decide()`, then a critic LLM that writes the feedback only when the answer is revise. The revision loop itself (max 2) lives in the team graph (stage 6).

## Files

| File | Contains |
| --- | --- |
| `src/brain/check.py` | `hard_validate()`, `run_check()`, `CheckResult` |
| `src/brain/prompts/check.py` | builds the `state` text for the judgement |
| `src/brain/prompts/critic.py` | the critic's messages and `CriticReport` schema |
| `src/brain/templates/registries.py` | adds a `validate()` to each `OutputType` |

## Interfaces

```python
# check.py
class CheckResult(BaseModel):
    passed: bool
    confidence: float          # probability the draft passes; becomes check_confidence
    feedback: list[str] = []   # for the next revision; empty when passed
    tokens: int = 0

def hard_validate(output_type: str, output: BaseModel) -> list[str]: ...   # error messages, [] if fine

async def run_check(
    deps: Deps, template: Template, task_type: str, output: BaseModel, lessons: list[Lesson]
) -> CheckResult: ...

# prompts/critic.py
class CriticReport(BaseModel):
    issues: list[str]          # short, concrete, each naming the broken rule or lesson
```

### Hard validators (per output type, in `registries.py`)

| Output | Fails when |
| --- | --- |
| `social_post` | text empty; over 300 characters |
| `email` | subject empty; body empty; `to` has no `@` |
| `report` | summary empty; no sources |
| all | any text field contains a placeholder: `[Name]`-style brackets, `{{…}}`, `<Company>`-style tags, `TODO`, `lorem ipsum` (case-insensitive) |

### `run_check` steps

1. `hard_validate` → errors → `CheckResult(passed=False, confidence=0, feedback=errors)`, no `decide()` and no critic.
2. `decide()` with one question, key `passes_check`:
   `Question(instructions="Does this draft follow every rule and lesson?", options={"pass": ..., "revise": ...})` (safe option last).
   The state lists the task type, the template's `check` rules, the active lessons and the draft's preview.
3. `confidence` = probability of pass: the decision's confidence if it chose `pass`, else 1 − confidence.
4. `passed` = `decision.accepts("pass", settings.decide_threshold)`.
5. Not passed → critic: `llm.structured(model_reflect, critic_messages, CriticReport)`. Empty issues or a failed call → feedback `["Make sure the draft follows: <rules>"]`.
6. `tokens` = decide tokens + critic tokens.

The check prompt never includes the lead's or the specialist's instructions.

## Tests

`tests/brain/test_check.py`

- `test_post_over_300_chars_fails_hard`: feedback mentions the limit; `FakeJev.calls` is empty.
- `test_email_without_subject_fails_hard`
- `test_placeholder_fails_hard` (parametrized: `[Name]`, `{{date}}`, `<Company>`, `TODO`, `Lorem ipsum`)
- `test_report_without_sources_fails_hard`
- `test_valid_drafts_pass_hard_validation`
- `test_pass_above_threshold`: Jev noul 0.9 → `passed`, confidence 0.9, no critic call.
- `test_pass_below_threshold_is_revise`: noul 0.6 → `passed=False`, critic called.
- `test_revise_uses_critic_issues_as_feedback`
- `test_revise_confidence_is_probability_of_pass`: noul 0.1 → confidence 0.1.
- `test_critic_failure_falls_back_to_rules`: `FakeLLM(fail=True)` → feedback lists the template's check rules.
- `test_state_contains_rules_lessons_and_draft`
- `test_state_excludes_lead_and_specialist_instructions`
- `test_tokens_are_counted`

## Done when

- [x] All tests above pass; earlier stages still pass
- [x] `ruff check` passes
- [x] Juan has reviewed the tests (Oct 8)

## Log

- Oct 8: implemented under `/goal implement stage 4`. Tests written first and run red (21 failed on stubs, 157 earlier tests passing), then implemented: 178 passed, `ruff check` clean. The tests were not reviewed before implementing; they are frozen from now on, and Juan's review is pending.
- Tests added beyond the list above: `test_markdown_link_is_not_a_placeholder` (`[Read more](https://…)` is allowed), `test_decide_failure_is_revise_with_zero_confidence`, `test_empty_critic_issues_fall_back_to_rules`, `test_critic_prompt_contains_rules_lessons_and_draft`. `test_email_without_subject_fails_hard` also covers an empty body and a `to` without `@`.
- Behaviour not in the spec: when `decide()` falls back to its safe default (Jev and the LLM both failed), `check_confidence` is 0, not 1 − 0. Otherwise a broken check would look like a sure pass to the promotion rule.
- `OutputType` gained `validate(output)`: the type's own rules (empty fields, the 300-character limit, `@` in `to`, at least one source) plus placeholders found in any string field. `POST_LIMIT` now lives in `registries.py`; `flows/approvals.py` imports it.
- The judgement prompt is `prompts/check.py` `review_text()` (task type, rules, lessons, draft preview) and the question `PASSES_CHECK`. The critic (`prompts/critic.py`) gets the same text, runs on `model_reflect`, and is capped at 5 issues.
