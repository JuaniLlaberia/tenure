# Stage 14: Schedules

**Status:** not started
**Depends on:** stage 9 (independent of stages 10–13)
**Spec:** [CONTRACT.md](../CONTRACT.md) §4 (`run_schedule`), §5 (scheduled runs), §6 (`ScheduleSaved`), §10 (schedule methods), §11 (`Cadence`, `Schedule`, `Task.schedule_id`); [ROADMAP.md](../ROADMAP.md) §5

## Goal

"Each Monday at 9, research an interesting topic around my business and propose a newsletter" becomes a `Schedule`, and the app runs it every Monday through `run_schedule`. "Stop the Monday newsletter" and "make it Tuesdays" work in chat. The app owns the clock; the brain owns understanding and running.

## Design

**Triage** gets two more `noul` questions in the same Jev call:

| Key | Question | Asked when |
| --- | --- | --- |
| `wants_schedule` | "Is the founder asking for something to happen repeatedly, on a schedule (every day, each Monday, monthly)?" | Always |
| `changes_schedule` | "Is the founder asking to stop, pause or change one of these schedules: <titles>?" | The team has active schedules |

Both use `DECIDE_THRESHOLD`. `wants_schedule` wins over routing: a scheduled request isn't also run now. The schedule card has "Run now", and the lead says so.

**New nodes**, after `triage`, before `plan`:

1. **`schedule`**: one structured call (`MODEL_LEAD`, `reasoning=False`) → `ScheduleDraft(title, request, every, weekday, day, hour, minute)`. Code validates:
   - A weekly cadence without a weekday becomes an `Ask` ("Which day?", quick replies Monday–Friday).
   - No time defaults to 9:00.
   - `request` must not be empty, and must have the repeat words stripped.
   - `timezone`: `profile.extra.get("timezone")`, else the `Cadence` default.

   Then it saves the `Schedule` (`next_run_at=None`; the app fills it), yields `ScheduleSaved`, and the lead says "Every Monday at 9:00 I'll research a topic and send you a newsletter draft. Tap Run now to see the first one today."
2. **`change_schedule`**: picks which schedule (a Jev `choice` over titles when more than one) and what to do: stop → `active=False`; change → the same structured call as above, keeping the `schedule_id`. It saves and yields `ScheduleSaved`.

**`TenureBrain.run_schedule(business_id, schedule_id)`**
- `get_schedule`; missing, inactive or another business's → `Error(recoverable=False)`.
- If the team thread has a pending interrupt, yield `Error(recoverable=True, message="Waiting for your answer first")`. The app shouldn't call it then, but the brain stays safe.
- Start a run on the team thread with `new_request(team, schedule.request, None)` plus `schedule_id`. The graph:
  - skips the schedule questions in triage, and routes only;
  - never interrupts (in `clarify` and `channels`, a scheduled run takes the safe reading and the lead's report says which one: "I sent this to the newsletter only; tell me if you want Bluesky too");
  - sets `Task.schedule_id` on every task;
  - adds the titles of this schedule's last 3 runs to the plan context ("Earlier runs: …; pick something new"). Keep them in team state as `schedule_history: dict[schedule_id, list[str]]`, in the checkpoint.
- Drafts wait for approval as usual; earned autonomy applies as usual.
- Usage rows carry the team and task as usual.

**Not the brain's job:** computing `next_run_at`, the loop, blocked threads, missed runs and the Stop button are all the app's (CONTRACT §5).

## Files

| File | Contains |
| --- | --- |
| `src/brain/prompts/lead.py` | `WANTS_SCHEDULE`, `CHANGES_SCHEDULE`; the schedule extraction prompt |
| `src/brain/graphs/team.py` | `schedule` and `change_schedule` nodes; `schedule_id` and `schedule_history` in state; no interrupts on scheduled runs |
| `src/brain/brain.py` | `run_schedule` |
| `src/brain/fakes.py` | `InMemoryStore.save_schedule`, `get_schedule`, `list_schedules` |
| `src/brain/cli.py` | `/run <schedule title>` |

## Tests

`tests/brain/test_schedules.py`

- `test_repeating_request_saves_a_schedule`: `ScheduleSaved` with `every="week"`, `weekday=0`, `hour=9`, and the request without "each Monday"; no tasks are created.
- `test_weekly_without_a_day_asks_which_day`
- `test_time_defaults_to_nine`
- `test_run_schedule_drafts_with_schedule_id`: every task has `schedule_id`; there's a `NeedsApproval`.
- `test_run_schedule_never_asks`: a request that would clarify takes the safe reading; no `Ask` in the stream.
- `test_run_schedule_avoids_last_topics`: the second run's plan prompt lists the first run's title.
- `test_run_schedule_rejects_inactive_or_foreign`
- `test_run_schedule_waits_for_a_pending_answer`
- `test_stop_in_chat_deactivates`: `ScheduleSaved` with `active=False`.
- `test_change_in_chat_keeps_the_id`
- `test_scheduled_run_respects_autonomy`: at `act_and_report`, the scheduled newsletter is sent and reported.

## Done when

- [ ] All tests above pass; earlier stages still pass
- [ ] `ruff check` passes
- [ ] Live: the Monday request gives the card; Run now gives a newsletter draft tagged with the schedule
- [ ] Juan has reviewed the tests

## Log

- Oct 9: stage written by Mark from the v0.6 planning session.
