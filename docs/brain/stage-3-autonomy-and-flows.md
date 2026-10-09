# Stage 3: Autonomy and flows (plain code)

**Status:** done
**Depends on:** stages 1, 2 (`Deps`)
**Spec:** [brain-engine.md](../specs/brain-engine.md) §6; [CONTRACT.md](../CONTRACT.md) §7 (decision table), §8 (autonomy), §9 (actions, undo)

## Goal

Everything that is rules, not judgement: trust and streaks, the gate, promotions, running an action with its audit entry, hiring, and the three flows outside the graph (`resolve_approval`, `respond_promotion`, `undo_action`). No LLM calls in this stage.

## Files

| File | Contains |
| --- | --- |
| `src/brain/flows/autonomy.py` | pure functions: gate, levels, streaks, promotion rule, initial trust |
| `src/brain/flows/actions.py` | `execute_action()` |
| `src/brain/flows/hire.py` | `hire_team()` flow (creates the team and trust rows) |
| `src/brain/flows/approvals.py` | `resolve_approval()` flow |
| `src/brain/flows/promotion.py` | `respond_promotion()` flow |
| `src/brain/flows/undo.py` | `undo_action()` flow |
| `tests/brain/conftest.py` | adds helpers: `hired_team(deps)`, `pending_approval(deps, ...)`, `fixed_clock` |

## Interfaces

```python
# flows/autonomy.py
STREAK_FOR_PROMOTION = 5
UNDO_WINDOW = timedelta(minutes=10)
Gate = Literal["approval", "act", "act_autonomous"]
def gate(level: AutonomyLevel) -> Gate: ...
def next_level(level: AutonomyLevel) -> AutonomyLevel | None: ...      # None at the top
def record_approve(trust: Trust, now: datetime) -> Trust: ...           # streak + 1
def reset_streak(trust: Trust, now: datetime) -> Trust: ...
def promotion_level(
    trust: Trust, recent: list[Approval], max_level: AutonomyLevel, min_confidence: float
) -> AutonomyLevel | None: ...                                          # the level to offer, or None
def initial_trust(team_id: str, template: Template, now: datetime) -> list[Trust]: ...

# flows/actions.py
async def execute_action(
    deps: Deps, task: Task, action: PlannedAction, *, approval_id: str | None, autonomous: bool
) -> ActionDone | Error: ...

# Flows are async generators of contract Events and never raise.
# flows/hire.py
async def hire_team(deps: Deps, business_id: str, template: str) -> AsyncIterator[Event]: ...
    # saves Team (onboarded=False) and one Trust per task type; yields TeamHired
# flows/approvals.py
LearnHook = Callable[[Approval, Literal["edit", "reject"], str], AsyncIterator[Event]]   # (approval, source, feedback)
ReviseHook = Callable[[Approval, str], AsyncIterator[Event]]                            # (approval, reason)
async def resolve_approval(
    deps: Deps, decision: ApprovalDecision, *, learn: LearnHook | None = None, revise: ReviseHook | None = None
) -> AsyncIterator[Event]: ...
# flows/promotion.py
async def respond_promotion(deps: Deps, response: PromotionResponse) -> AsyncIterator[Event]: ...
# flows/undo.py
async def undo_action(deps: Deps, business_id: str, action_id: str) -> AsyncIterator[Event]: ...
```

The `learn` and `revise` hooks are filled in at stage 7 (reflect) and stage 6 (re-entering the team graph). Here they are optional, so these flows can be tested alone.

### Rules

**Gate:** `draft_only`, `act_after_approval` → `approval`; `act_and_report` → `act`; `autonomous` → `act_autonomous`.

**Promotion** (`promotion_level`): returns `next_level(trust.level)` only if `trust.approval_streak ≥ trust.promote_after`, the `promote_after` most recent resolved approvals are all `approved` with `check_confidence ≥ min_confidence`, and the next level is ≤ `max_level`. (Was a fixed 5 until contract v0.3; see the log.)

**`execute_action`:** calls `post_social` or `send_email` and always writes an `AuditEntry` (also when `ok=False`).

- `undo_until = now + 10 min` only for a successful `post_social`, otherwise `None`.
- Summaries: `"Posted to Bluesky"`, `"Sent the email to {to}"`.
- Failure → `Error(recoverable=True, message="I couldn't post to Bluesky: {error}")` (or "send the email").

**`resolve_approval`:**

| Case | What happens |
| --- | --- |
| Approval missing, or `business_id` doesn't match | `Error("I couldn't find that draft.", recoverable=True)` |
| Not pending | `Error("That draft was already handled.", recoverable=True)` |
| `edit` without `edited_text` | recoverable `Error` |
| `approve` | runs `planned_action` if any → on failure yield the `Error` and stop (approval stays pending, so the founder can retry). Then approval `approved`, task `DONE`, streak + 1, lead `Say`, and a `PromotionOffer` if `promotion_level` says so |
| `edit` | builds the action from `edited_text` (post: `PostSocial(text=edited_text)`, over 300 characters → recoverable `Error`, approval stays pending; email: same `to` and `subject`, new body), runs it, approval `edited` with `edited_text`, task `DONE`, streak 0, then `learn(approval, "edit", "Original:\n…\n\nEdited:\n…")`, lead `Say` |
| `reject` | approval `rejected` with `reason`, streak 0. With a reason: `learn(approval, "reject", "Draft:\n…\n\nRejected because: …")`; then if `revise` is set and `task.revisions < 2` → `revise(approval, reason)`. Otherwise task `REJECTED` and a lead `Say` |

All `resolved_at` and `updated_at` come from `deps.clock()`. The lead persona comes from the team's template.

**`respond_promotion`:** trust row missing → recoverable `Error`. Accept → level = next level (capped at the template's `max_level`), streak 0, lead `Say` describing the new level. Decline → streak 0, lead `Say`.

**`undo_action`:** CONTRACT §9. Missing action or other business → recoverable `Error`; `undo_until` is `None` (emails) or past, or `undone_at` is set → recoverable `Error`. Otherwise `delete_social`; on success log a `delete_social` `AuditEntry`, set `undone_at` on the original, reset the streak for that task type, yield `ActionUndone`; on failure a recoverable `Error`.

## Tests

`tests/brain/test_autonomy.py`

- `test_gate_per_level` (parametrized over the 4 levels)
- `test_next_level_climbs_the_ladder_and_stops_at_the_top`
- `test_record_approve_and_reset_streak`
- `test_promotion_offered_at_streak_five`
- `test_no_promotion_below_streak_five`
- `test_no_promotion_with_one_low_confidence`
- `test_no_promotion_with_an_edit_among_the_last_five`
- `test_no_promotion_at_max_level`
- `test_initial_trust_one_row_per_task_type_at_start_level`

`tests/brain/test_actions.py`

- `test_post_runs_and_is_audited_with_undo_window`
- `test_email_runs_and_is_never_undoable`
- `test_failed_action_is_audited_and_returns_error`
- `test_autonomous_flag_is_recorded`

`tests/brain/test_hire_flow.py`

- `test_hire_saves_team_and_trust_and_yields_team_hired`: personas lead first, `onboarded=False`.
- `test_hire_unknown_template_yields_error`

`tests/brain/test_approvals_flow.py`

- `test_approve_runs_action_and_marks_done`: `ActionDone` with undo window, approval `approved`, task `DONE`, audit has `approval_id`.
- `test_approve_increments_streak`
- `test_approve_without_planned_action_runs_nothing` (`draft_only` / `competitor_check`)
- `test_approve_offers_promotion_after_five`: four earlier approvals at 0.9 seeded, the fifth yields `PromotionOffer` from `act_after_approval` to `act_and_report`.
- `test_approve_failed_action_keeps_approval_pending`
- `test_edit_posts_edited_text_and_resets_streak`
- `test_edit_email_keeps_to_and_subject`
- `test_edit_over_300_chars_yields_error`
- `test_edit_calls_learn_hook_with_original_and_edited`
- `test_edit_without_text_yields_error`
- `test_reject_without_reason_marks_rejected`
- `test_reject_with_reason_calls_learn_then_revise`
- `test_reject_with_reason_and_no_revisions_left_marks_rejected`: `task.revisions == 2` → `revise` is not called.
- `test_already_resolved_yields_error`
- `test_unknown_or_foreign_approval_yields_error`
- `test_flow_never_raises`: a Store that raises → one `Error`, no exception.

`tests/brain/test_promotion_flow.py`

- `test_accept_moves_up_one_level_and_resets_streak`
- `test_decline_resets_streak_and_keeps_level`
- `test_decline_then_next_approve_does_not_reoffer`
- `test_missing_trust_yields_error`

`tests/brain/test_undo_flow.py`

- `test_undo_inside_window_deletes_and_logs`: `delete_social` called with the external id, a `delete_social` audit entry, `undone_at` set, `ActionUndone` yielded.
- `test_undo_resets_streak`
- `test_undo_outside_window_yields_error` (clock moved 11 minutes)
- `test_undo_twice_yields_error`
- `test_undo_email_yields_error`
- `test_undo_failed_delete_yields_error`

## Done when

- [x] All tests above pass; earlier stages still pass
- [x] `ruff check` passes
- [x] Juan has reviewed the tests (Oct 8)

## Log

- Oct 8: implemented under `/goal implement stage 3`. Tests written first and run red (49 failed on stubs, 108 earlier tests passing), then implemented: 157 passed, `ruff check` clean. The tests were not reviewed before implementing; they are frozen from now on, and Juan's review is pending.
- Tests added beyond the list above: `test_no_promotion_with_fewer_than_five_resolved` (autonomy); `test_reject_with_reason_without_revise_hook_marks_rejected` (approvals); `test_accept_at_max_level_keeps_level`, `test_other_business_yields_error` (promotion); `test_unknown_or_foreign_action_yields_error` (undo).
- `tests/brain/conftest.py` gained: a movable `Clock` (the `clock` fixture; `clock.advance(minutes=11)`), `collect` (drains an event stream), `make_team(business_id, template, levels, streaks)` (saves an onboarded team plus trust rows), `make_approval(team, task_type, planned_action, confidence, revisions, status)` (saves a task plus an approval), and the `POST` / `EMAIL` sample actions.
- New shared helper: `brain.common.guarded(events, team_id)` wraps a stream so any exception becomes one recoverable `Error` with `GENERIC_ERROR`. Every flow's public function uses it; the facade (stage 6) should too.
- `flows/autonomy.py` adds `is_above(level, cap)`; `flows/approvals.py` has `MAX_REVISIONS = 2`, `POST_LIMIT = 300` and `label(task_type)` ("social_post" → "social post"), reused by `promotion.py`.
- `execute_action` turns an exception from a tool into a failed `ActionResult` (`error` = exception class name), so a raising tool is still audited.
- A failed delete during undo is also written to the audit log (`summary="Failed to delete the Bluesky post"`), and the original keeps `undone_at=None`.
- Order of events: approve → `ActionDone`, lead `Say`, optional `PromotionOffer`; edit → `ActionDone`, the `learn` hook's events, `Say`; reject with a reason → the `learn` hook's events, then the `revise` hook's events (no `Say` of its own), else `Say`.
- Oct 8, contract v0.3 (Juan OK'd it by asking for the change): `STREAK_FOR_PROMOTION` is gone. `promotion_level` uses `trust.promote_after`; `resolve_approval` reads `recent_approvals(limit=trust.promote_after)` and names that number in the evidence. New trust rows keep the contract default (5); approve, promotion and undo keep the founder's value because they copy the row. New tests in `tests/brain/test_promote_after.py` (8, run red first: 4 failed on the fixed 5); the approved stage 3 tests are unchanged and still pass with the default. `make_team` in `conftest.py` gained a `promote_after` option.
- Oct 9, contract v0.4 (Mark's PR #2, merged from `dev`): an edit may carry `edited_action`, the founder's whole version. `_edit` runs it when the draft has an action and the `tool` matches (mismatch → recoverable `Error`, approval stays pending), takes the post text or email body from it as `edited_text` (it wins over a different `edited_text`), and ignores it for drafts without an action. The `learn` hook gets the whole edited email (`To`, `Subject`, body). New tests in `tests/brain/test_edit_action.py` (7, run red first: 5 failed); the approved stage 3 tests are unchanged.
