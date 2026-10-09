from datetime import timedelta

import pytest

from brain.cli import Cli
from brain.common import new_id
from brain.prompts.lead import ScheduleDraft
from contract import (
    ActionDone,
    Ask,
    AutonomyLevel,
    BusinessProfile,
    Cadence,
    Error,
    NeedsApproval,
    Say,
    Schedule,
    ScheduleSaved,
)

from .conftest import NOW

REQUEST = "Research an interesting topic around my business and propose a newsletter"
MONDAYS = f"Each Monday at 9, {REQUEST[0].lower()}{REQUEST[1:]}"
DRAFT = {
    "title": "Monday newsletter idea",
    "request": REQUEST,
    "every": "week",
    "weekday": 0,
    "hour": 9,
    "minute": 0,
}

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def plan_prompts(llm):
    return [
        str(call.messages)
        for call in llm.calls
        if call.schema and call.schema.__name__ == "LeadPlan"
    ]

@pytest.fixture
async def team(make_team):
    return await make_team()

@pytest.fixture
def wants(script, jev, llm):
    """
    Scripts a message that asks for a schedule, with the extraction `draft`.
    """

    def wants(draft=DRAFT, **kwargs):
        script(**kwargs)
        jev.answers["wants_schedule"] = 0.95
        llm.structured_responses["ScheduleDraft"] = draft

    return wants

@pytest.fixture
def saved(deps, team):
    async def saved(title="Monday newsletter idea", active=True, business_id=None, created=0):
        schedule = Schedule(
            schedule_id=new_id(),
            business_id=business_id or team.business_id,
            team_id=team.team_id,
            title=title,
            request=REQUEST,
            cadence=Cadence(every="week", weekday=0),
            active=active,
            created_at=NOW + timedelta(minutes=created),
        )
        await deps.store.save_schedule(schedule)
        return schedule

    return saved

async def test_repeating_request_saves_a_schedule(
    brain, collect, deps, llm, wants, team, message
):
    wants()

    events = await collect(brain.handle_message(message(team, MONDAYS)))

    (event,) = of(events, ScheduleSaved)
    schedule = event.schedule
    assert (schedule.cadence.every, schedule.cadence.weekday) == ("week", 0)
    assert (schedule.cadence.hour, schedule.cadence.minute) == (9, 0)
    assert schedule.request == REQUEST
    assert "each monday" not in schedule.request.lower()
    assert (schedule.team_id, schedule.active, schedule.next_run_at) == (team.team_id, True, None)
    assert event.persona.name == "Maya" and event.team_id == team.team_id
    assert await deps.store.get_schedule(schedule.schedule_id) == schedule
    assert not of(events, NeedsApproval) and deps.store.tasks == {}
    (say,) = of(events, Say)
    assert "Every Monday at 9:00" in say.text and "Run now" in say.text
    (call,) = [c for c in llm.calls if c.schema is ScheduleDraft]
    assert (call.model, call.reasoning) == (deps.settings.model_lead, False)
    assert MONDAYS in str(call.messages)

async def test_weekly_without_a_day_asks_which_day(brain, collect, wants, team, message):
    wants({**DRAFT, "weekday": None})

    asked = await collect(brain.handle_message(message(team, "Every week, a newsletter idea")))

    (ask,) = of(asked, Ask)
    assert ask.question == "Which day?"
    assert ask.quick_replies == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert not of(asked, ScheduleSaved)

    answered = await collect(brain.handle_message(message(team, "Tuesday", "m2")))

    (event,) = of(answered, ScheduleSaved)
    assert event.schedule.cadence.weekday == 1

async def test_time_defaults_to_nine(brain, collect, wants, team, message):
    wants({"title": "Daily idea", "request": "Suggest one post idea", "every": "day"})

    events = await collect(brain.handle_message(message(team, "Every day, suggest a post idea")))

    (event,) = of(events, ScheduleSaved)
    assert (event.schedule.cadence.hour, event.schedule.cadence.minute) == (9, 0)
    assert "Every day at 9:00" in of(events, Say)[0].text

async def test_stated_timezone_is_kept(brain, collect, deps, wants, llm, team, message):
    wants()
    llm.structured_responses["ScheduleDraft"] = [
        {**DRAFT, "timezone": "Europe/Madrid"},
        {**DRAFT, "timezone": "Mars/Olympus"},
        {**DRAFT, "timezone": None},
    ]

    first = await collect(brain.handle_message(message(team, f"{MONDAYS}, Madrid time")))
    second = await collect(brain.handle_message(message(team, MONDAYS, "m2")))
    await deps.store.save_profile(
        BusinessProfile(
            business_id="b1",
            name="Studio",
            what_you_sell="Logos",
            customers="Cafés",
            extra={"timezone": "Europe/London"},
        )
    )
    third = await collect(brain.handle_message(message(team, MONDAYS, "m3")))

    runs = (first, second, third)
    zones = [of(events, ScheduleSaved)[0].schedule.cadence.timezone for events in runs]
    assert zones == ["Europe/Madrid", "America/Los_Angeles", "Europe/London"]

async def test_run_schedule_drafts_with_schedule_id(
    brain, collect, deps, jev, script, saved, team
):
    schedule = await saved()
    script(task_types=("newsletter",))

    events = await collect(brain.run_schedule(team.business_id, schedule.schedule_id))

    (needs,) = of(events, NeedsApproval)
    task = await deps.store.get_task(needs.task_id)
    assert task.schedule_id == schedule.schedule_id
    assert all(t.schedule_id == schedule.schedule_id for t in deps.store.tasks.values())
    _, state, questions = jev.calls[0]
    assert REQUEST in state
    assert not {"wants_schedule", "changes_schedule", "has_feedback"} & set(questions)
    assert not of(events, ScheduleSaved)

async def test_run_schedule_never_asks(brain, collect, deps, script, saved, team):
    schedule = await saved()
    script(
        task_types=("newsletter",),
        named=0.1,
        clear=0.1,
        question="Which product should the newsletter feature?",
    )

    events = await collect(brain.run_schedule(team.business_id, schedule.schedule_id))

    assert not of(events, Ask)
    assert of(events, NeedsApproval)
    report = of(events, Say)[-1].text
    assert "Which product should the newsletter feature?" in report
    snapshot = await brain.team_graph.aget_state(brain._config(team))
    assert not snapshot.interrupts

async def test_run_schedule_avoids_last_topics(brain, collect, llm, script, saved, team):
    schedule = await saved()
    script(task_types=("newsletter",))

    await collect(brain.run_schedule(team.business_id, schedule.schedule_id))
    await collect(brain.run_schedule(team.business_id, schedule.schedule_id))

    first, second = plan_prompts(llm)
    assert "Earlier runs" not in first
    assert "Earlier runs" in second and "Launch newsletter" in second

async def test_run_schedule_rejects_inactive_or_foreign(brain, collect, jev, saved, team):
    stopped = await saved(active=False)
    foreign = await saved(business_id="other")

    for business_id, schedule_id in [
        (team.business_id, "no-such-schedule"),
        (team.business_id, stopped.schedule_id),
        (team.business_id, foreign.schedule_id),
    ]:
        events = await collect(brain.run_schedule(business_id, schedule_id))
        (error,) = of(events, Error)
        assert error.recoverable is False
    assert jev.calls == []

async def test_run_schedule_waits_for_a_pending_answer(
    brain, collect, script, saved, team, message
):
    schedule = await saved()
    script(clear=0.1, question="What date is the launch?")
    asked = await collect(brain.handle_message(message(team, "Post about our launch")))
    assert of(asked, Ask)

    events = await collect(brain.run_schedule(team.business_id, schedule.schedule_id))

    (error,) = of(events, Error)
    assert error.recoverable is True
    assert error.message == "Waiting for your answer first"
    assert not of(events, NeedsApproval)

async def test_stop_in_chat_deactivates(brain, collect, deps, jev, script, saved, team, message):
    schedule = await saved()
    script(work=0.1)
    jev.answers.update({"changes_schedule": 0.95, "stops_schedule": 0.95})

    events = await collect(brain.handle_message(message(team, "Stop the Monday newsletter")))

    (event,) = of(events, ScheduleSaved)
    assert event.schedule.schedule_id == schedule.schedule_id
    assert event.schedule.active is False
    assert (await deps.store.get_schedule(schedule.schedule_id)).active is False
    assert deps.store.tasks == {}
    _, state, questions = jev.calls[0]
    assert "Monday newsletter idea" in questions["changes_schedule"]["instructions"]

async def test_change_in_chat_keeps_the_id(
    brain, collect, deps, jev, llm, script, saved, team, message
):
    schedule = await saved()
    script(work=0.1)
    jev.answers.update({"changes_schedule": 0.95, "stops_schedule": 0.05})
    llm.structured_responses["ScheduleDraft"] = {"weekday": 1}

    events = await collect(brain.handle_message(message(team, "Make it Tuesdays")))

    (event,) = of(events, ScheduleSaved)
    changed = event.schedule
    assert changed.schedule_id == schedule.schedule_id
    assert (changed.cadence.weekday, changed.cadence.hour) == (1, 9)
    assert (changed.request, changed.title, changed.active) == (REQUEST, schedule.title, True)
    assert changed.created_at == schedule.created_at
    assert "Every Tuesday at 9:00" in of(events, Say)[0].text

async def test_which_schedule_is_a_choice_when_there_are_several(
    brain, collect, deps, jev, script, saved, team, message
):
    older = await saved(title="Monday newsletter idea")
    newer = await saved(title="Friday post", created=5)
    script(work=0.1)
    jev.answers.update(
        {"changes_schedule": 0.95, "stops_schedule": 0.95, "which_schedule": "s2"}
    )

    events = await collect(brain.handle_message(message(team, "Stop the Monday one")))

    (event,) = of(events, ScheduleSaved)
    assert event.schedule.schedule_id == older.schedule_id
    assert (await deps.store.get_schedule(newer.schedule_id)).active is True
    (question,) = [q for _, _, qs in jev.calls for key, q in qs.items() if key == "which_schedule"]
    assert "Friday post" in str(question) and "Monday newsletter idea" in str(question)

async def test_scheduled_run_respects_autonomy(
    brain, collect, tools, script, make_team, deps
):
    team = await make_team(levels={"newsletter": AutonomyLevel.ACT_AND_REPORT})
    schedule = Schedule(
        schedule_id=new_id(),
        business_id=team.business_id,
        team_id=team.team_id,
        title="Monday newsletter idea",
        request=REQUEST,
        cadence=Cadence(every="week", weekday=0),
        created_at=NOW,
    )
    await deps.store.save_schedule(schedule)
    script(task_types=("newsletter",))

    events = await collect(brain.run_schedule(team.business_id, schedule.schedule_id))

    assert not of(events, NeedsApproval)
    (done,) = of(events, ActionDone)
    assert done.autonomous is False
    assert [name for name, _ in tools.calls].count("send_email") == 1

async def test_store_lists_schedules_newest_first(deps, saved, team):
    older = await saved()
    newer = await saved(title="Friday post", created=5)
    await saved(title="Elsewhere", business_id="other")

    listed = await deps.store.list_schedules(team.business_id)

    assert [s.schedule_id for s in listed] == [newer.schedule_id, older.schedule_id]
    assert await deps.store.list_schedules(team.business_id, team_id="nope") == []

async def test_cli_runs_a_schedule_by_title(store, saved, team):
    schedule = await saved()
    ran = []

    class Recording:
        def run_schedule(self, business_id, schedule_id):
            ran.append((business_id, schedule_id))
            return _nothing()

    shell = Cli(Recording(), store, business_id=team.business_id, out=lambda _: None)

    await shell.handle("/run monday newsletter")

    assert ran == [(team.business_id, schedule.schedule_id)]

async def _nothing():
    return
    yield
