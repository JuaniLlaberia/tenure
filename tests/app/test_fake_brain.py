from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.fake_brain import PROMOTION_STREAK, TOKENS_PER_STEP, FakeBrain
from app.store.memory import InMemoryStore
from contract import (
    ActionDone,
    ActionUndone,
    ApprovalDecision,
    Ask,
    AutonomyLevel,
    Error,
    Event,
    IncomingMessage,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    PostSocial,
    Progress,
    PromotionOffer,
    PromotionResponse,
    Say,
    ScheduleSaved,
    SendEmail,
    TeamHired,
)

BIZ = "b1"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
REQUEST = "We launch our new coaching package on Friday, get the word out"

class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def brain(store: InMemoryStore, clock: Clock) -> FakeBrain:
    return FakeBrain(store, clock=clock)

async def collect(events) -> list[Event]:
    return [event async for event in events]

def msg(text: str, team_id: str | None = None) -> IncomingMessage:
    return IncomingMessage(
        business_id=BIZ, team_id=team_id, text=text, message_id=str(uuid4()), sent_at=NOW
    )

def of(events: list[Event], kind: type) -> list:
    return [event for event in events if isinstance(event, kind)]

async def onboard_business(brain: FakeBrain) -> list[Event]:
    events = await collect(brain.start_onboarding(BIZ))
    for answer in ["Bright Coaching", "Career coaching", "Mid-career engineers"]:
        events += await collect(brain.handle_message(msg(answer)))
    return events

async def hired_team(brain: FakeBrain, template: str = "marketing") -> str:
    await onboard_business(brain)
    events = await collect(brain.hire_team(BIZ, template))
    team_id = events[0].team_id
    asks = 1
    while True:
        events = await collect(brain.handle_message(msg("Bluesky", team_id)))
        if of(events, OnboardingComplete):
            return team_id
        asks += 1
        assert asks < 10

async def drafts(brain: FakeBrain, team_id: str, text: str = REQUEST) -> list[NeedsApproval]:
    return of(await collect(brain.handle_message(msg(text, team_id))), NeedsApproval)

def decide(approval_id: str, decision: str, **fields) -> ApprovalDecision:
    return ApprovalDecision(business_id=BIZ, approval_id=approval_id, decision=decision, **fields)

async def approve(brain: FakeBrain, approval_id: str) -> list[Event]:
    return await collect(brain.resolve_approval(decide(approval_id, "approve")))

async def say(brain: FakeBrain, text: str, team_id: str | None = None) -> list[Event]:
    return await collect(brain.handle_message(msg(text, team_id)))

def test_list_templates_has_marketing_with_lead_first(brain):
    templates = {template.name: template for template in brain.list_templates()}
    marketing = templates["marketing"]
    assert marketing.personas[0].role == "Marketing lead"
    assert "social_post" in marketing.task_types

async def test_business_onboarding_asks_until_complete(brain):
    events = await onboard_business(brain)
    assert isinstance(events[0], Ask)
    assert len(of(events, Ask)) == 3
    assert events[-1] == OnboardingComplete(scope="business", team_id=None)

    after = await collect(brain.handle_message(msg("hello")))
    assert [type(event) for event in after] == [Say]

async def test_hire_team_yields_team_hired_first_then_asks(brain):
    events = await collect(brain.hire_team(BIZ, "marketing"))
    assert isinstance(events[0], TeamHired)
    assert isinstance(events[-1], Ask)
    assert events[-1].team_id == events[0].team_id

async def test_hire_unknown_template_yields_error(brain):
    events = await collect(brain.hire_team(BIZ, "nope"))
    assert [type(event) for event in events] == [Error]

async def test_work_request_streams_progress_and_two_drafts(brain):
    team_id = await hired_team(brain)
    events = await collect(brain.handle_message(msg(REQUEST, team_id)))
    assert of(events, Progress)
    post, email = of(events, NeedsApproval)
    assert isinstance(post.planned_action, PostSocial)
    assert isinstance(email.planned_action, SendEmail)
    assert post.preview == post.planned_action.text
    assert isinstance(events[-1], Say)

async def test_long_request_still_fits_bluesky_limit(brain):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id, "launch " * 100)
    assert len(post.planned_action.text) <= 300

async def test_short_request_asks_then_works_with_the_answer(brain):
    team_id = await hired_team(brain)
    events = await collect(brain.handle_message(msg("post something", team_id)))
    assert [type(event) for event in events] == [Ask]
    assert await drafts(brain, team_id, "It's for this week's launch")

async def test_chat_feedback_becomes_lesson(brain):
    team_id = await hired_team(brain)
    team_lesson = of(await say(brain, "Stop using hashtags", team_id), LessonLearned)
    business_lesson = of(await say(brain, "We never offer discounts", team_id), LessonLearned)
    assert not team_lesson[0].business_wide
    assert business_lesson[0].business_wide

async def test_approve_post_then_undo_within_window(brain, clock):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    done = of(await approve(brain, post.approval_id), ActionDone)[0]
    assert done.undo_until == NOW + timedelta(minutes=10)
    assert not done.autonomous

    clock.now = NOW + timedelta(minutes=5)
    events = await collect(brain.undo_action(BIZ, done.action_id))
    assert events == [
        ActionUndone(action_id=done.action_id, team_id=team_id, summary="Deleted the Bluesky post")
    ]
    again = await collect(brain.undo_action(BIZ, done.action_id))
    assert [type(event) for event in again] == [Error]

async def test_undo_after_window_fails(brain, clock):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    done = of(await approve(brain, post.approval_id), ActionDone)[0]
    clock.now = NOW + timedelta(minutes=11)
    events = await collect(brain.undo_action(BIZ, done.action_id))
    assert [type(event) for event in events] == [Error]

async def test_email_is_never_undoable(brain):
    team_id = await hired_team(brain)
    _, email = await drafts(brain, team_id)
    events = await collect(brain.resolve_approval(decide(email.approval_id, "approve")))
    done = of(events, ActionDone)[0]
    assert done.undo_until is None
    undo = await collect(brain.undo_action(BIZ, done.action_id))
    assert [type(event) for event in undo] == [Error]

async def test_resolving_twice_or_for_another_business_fails(brain):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    other = ApprovalDecision(business_id="b2", approval_id=post.approval_id, decision="approve")
    assert [type(e) for e in await collect(brain.resolve_approval(other))] == [Error]

    await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
    again = await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
    assert [type(event) for event in again] == [Error]

async def test_edit_posts_edited_text_and_learns(brain):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    too_long = await collect(
        brain.resolve_approval(decide(post.approval_id, "edit", edited_text="x" * 301))
    )
    assert [type(event) for event in too_long] == [Error]

    events = await collect(
        brain.resolve_approval(decide(post.approval_id, "edit", edited_text="Launching Friday."))
    )
    assert of(events, ActionDone)
    assert of(events, LessonLearned)

async def test_reject_with_reason_revises_until_out_of_revisions(brain):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    approval_id = post.approval_id
    for _ in range(2):
        events = await collect(
            brain.resolve_approval(decide(approval_id, "reject", reason="Too salesy"))
        )
        assert of(events, LessonLearned)
        revised = of(events, NeedsApproval)[0]
        assert revised.approval_id != approval_id
        assert revised.task_id == post.task_id
        approval_id = revised.approval_id

    final = await collect(brain.resolve_approval(decide(approval_id, "reject", reason="Still no")))
    assert not of(final, NeedsApproval)

async def test_reject_without_reason_drops_the_task(brain):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    events = await collect(brain.resolve_approval(decide(post.approval_id, "reject")))
    assert [type(event) for event in events] == [Say]

async def test_promotion_after_streak_then_acts_alone(brain):
    team_id = await hired_team(brain)
    offers = []
    for _ in range(PROMOTION_STREAK):
        post, _ = await drafts(brain, team_id)
        events = await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
        offers += of(events, PromotionOffer)
    offer = offers[0]
    assert offer.task_type == "social_post"
    assert offer.proposed_level == AutonomyLevel.ACT_AND_REPORT

    response = PromotionResponse(
        business_id=BIZ, team_id=team_id, task_type="social_post", accepted=True
    )
    await collect(brain.respond_promotion(response))

    events = await collect(brain.handle_message(msg(REQUEST, team_id)))
    assert of(events, ActionDone)[0].autonomous
    assert [d.task_type for d in of(events, NeedsApproval)] == ["newsletter"]

async def test_promotion_streak_is_configurable(clock):
    brain = FakeBrain(promotion_streak=1, clock=clock)
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    assert of(await approve(brain, post.approval_id), PromotionOffer)

async def test_no_promotion_before_the_streak_is_reached(brain):
    team_id = await hired_team(brain)
    for _ in range(PROMOTION_STREAK - 1):
        post, _ = await drafts(brain, team_id)
        assert not of(await approve(brain, post.approval_id), PromotionOffer)

async def test_declined_promotion_resets_the_streak(brain):
    team_id = await hired_team(brain)
    for _ in range(PROMOTION_STREAK):
        post, _ = await drafts(brain, team_id)
        await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
    response = PromotionResponse(
        business_id=BIZ, team_id=team_id, task_type="social_post", accepted=False
    )
    await collect(brain.respond_promotion(response))

    post, _ = await drafts(brain, team_id)
    events = await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
    assert not of(events, PromotionOffer)

async def test_competitor_check_reports_without_approval(brain):
    team_id = await hired_team(brain)
    events = await say(brain, "What are my competitors doing lately?", team_id)
    assert not of(events, NeedsApproval)
    assert any("Competitor check" in event.text for event in of(events, Say))

async def test_finance_team_drafts_a_reminder_email(brain):
    team_id = await hired_team(brain, "finance")
    (reminder,) = await drafts(brain, team_id, "Remind Acme about the overdue invoice please")
    assert reminder.task_type == "invoice_reminder"
    assert isinstance(reminder.planned_action, SendEmail)

async def test_unknown_team_yields_error(brain):
    events = await collect(brain.handle_message(msg("hello", "missing")))
    assert [type(event) for event in events] == [Error]

async def test_errors_inside_a_stream_become_error_events(brain):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    async def broken(team_id):
        raise RuntimeError("database down")

    brain._store.get_team = broken
    events = await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
    assert [type(event) for event in events] == [Error]

async def test_a_scripted_session_covers_every_event_type(brain):
    seen = set(type(event) for event in await onboard_business(brain))
    hired = await collect(brain.hire_team(BIZ, "marketing"))
    seen |= set(map(type, hired))
    team_id = hired[0].team_id
    for answer in ["Bluesky", "Friday launch", "list@example.com"]:
        seen |= set(map(type, await collect(brain.handle_message(msg(answer, team_id)))))
    for _ in range(PROMOTION_STREAK):
        events = await collect(brain.handle_message(msg(REQUEST, team_id)))
        seen |= set(map(type, events))
        post = of(events, NeedsApproval)[0]
        events = await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
        seen |= set(map(type, events))
    action = of(events, ActionDone)[0]
    seen |= set(map(type, await collect(brain.undo_action(BIZ, action.action_id))))
    seen |= set(map(type, await collect(brain.handle_message(msg("Stop using emojis", team_id)))))
    weekly = msg("Every Monday, research a topic and propose a newsletter", team_id)
    seen |= set(map(type, await collect(brain.handle_message(weekly))))
    seen |= set(map(type, await collect(brain.hire_team(BIZ, "nope"))))
    assert seen == set(Event.__origin__.__args__)

async def test_records_land_in_the_store(brain, store):
    team_id = await hired_team(brain)
    profile = await store.get_profile(BIZ)
    assert profile.name == "Bright Coaching"
    team = await store.get_team(team_id)
    assert team.onboarded and team.template == "marketing"
    facts = await store.list_lessons(BIZ, team_id)
    assert {lesson.key for lesson in facts} == {"channels", "upcoming", "newsletter_to"}
    assert all(lesson.kind == "fact" for lesson in facts)

    post, email = await drafts(brain, team_id)
    task = await store.get_task(post.task_id)
    assert task.status == "waiting_approval"
    assert task.tokens_used == TOKENS_PER_STEP
    assert (await store.get_approval(post.approval_id)).status == "pending"

    events = await approve(brain, post.approval_id)
    done = of(events, ActionDone)[0]
    entry = await store.get_action(done.action_id)
    assert entry.approval_id == post.approval_id and entry.tool == "post_social"
    assert (await store.get_task(post.task_id)).status == "done"
    assert (await store.get_trust(team_id, "social_post")).approval_streak == 1
    resolved = await store.recent_approvals(team_id, "social_post")
    assert [a.approval_id for a in resolved] == [post.approval_id]

    await collect(brain.undo_action(BIZ, done.action_id))
    assert (await store.get_action(done.action_id)).undone_at == NOW
    assert (await store.get_trust(team_id, "social_post")).approval_streak == 0

async def test_lessons_are_saved_with_their_scope(brain, store):
    team_id = await hired_team(brain)
    await say(brain, "Stop using hashtags", team_id)
    await say(brain, "We never offer discounts", team_id)
    preferences = [
        lesson for lesson in await store.list_lessons(BIZ, team_id) if lesson.kind == "preference"
    ]
    scopes = {lesson.text: lesson.team_id for lesson in preferences}
    assert scopes == {"Stop using hashtags": team_id, "We never offer discounts": None}

async def test_reanswering_onboarding_replaces_the_fact(brain, store, clock):
    team_id = await hired_team(brain)
    brain._threads[team_id].onboarding_step = 0
    team = await store.get_team(team_id)
    await store.save_team(team.model_copy(update={"onboarded": False}))
    await say(brain, "Only LinkedIn", team_id)
    channels = [
        lesson for lesson in await store.list_lessons(BIZ, team_id) if lesson.key == "channels"
    ]
    assert [lesson.text for lesson in channels] == ["Channels: Only LinkedIn"]

async def test_a_new_brain_on_the_same_store_carries_on(brain, store, clock):
    team_id = await hired_team(brain)
    post, _ = await drafts(brain, team_id)
    restarted = FakeBrain(store, clock=clock)
    assert of(await approve(restarted, post.approval_id), ActionDone)
    assert len(await drafts(restarted, team_id)) == 2

async def test_a_repeating_request_saves_a_schedule_that_runs_on_demand(brain):
    team_id = await hired_team(brain)
    weekly = msg("Every Monday, research a topic and propose a newsletter", team_id)
    (saved,) = await collect(brain.handle_message(weekly))
    assert isinstance(saved, ScheduleSaved)
    assert (saved.schedule.cadence.every, saved.schedule.cadence.weekday) == ("week", 0)

    events = await collect(brain.run_schedule(BIZ, saved.schedule.schedule_id))
    assert of(events, NeedsApproval)
