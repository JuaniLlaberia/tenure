"""
Founders off the happy path in Telegram: forgotten prompts, failed posts, busy teams, deleted
topics, missing rights and drafts with a send time.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from tests.app.fakes import NOW, Clock, FakeBluesky, FakeChat
from tests.app.test_flows import CHAT, REQUEST, drafts, marketing_team, say, setup_business, tap

from app.chat import ui
from app.chat.flows import Flows, Refused
from app.fake_brain import FakeBrain
from app.files import Files
from app.store.memory import InMemoryStore
from app.tools.real import RealTools
from contract import IncomingMessage, NeedsApproval, Say, TaskStatus

TIMED = "We launch on Friday, post about it on Friday at 6pm"

class Gated(FakeBrain):
    """
    A fake brain whose team runs wait at `gate` until a test opens it.
    """

    def __init__(self, store: InMemoryStore, **kwargs) -> None:
        super().__init__(store, **kwargs)
        self.gate = asyncio.Event()
        self.gate.set()
        self.seen: list[str] = []

    def handle_message(self, msg: IncomingMessage):
        inner = super().handle_message(msg)

        async def run():
            self.seen.append(msg.text)
            await self.gate.wait()
            async for event in inner:
                yield event

        return run()

@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def bluesky() -> FakeBluesky:
    return FakeBluesky()

@pytest.fixture
def tools(store, clock, bluesky) -> RealTools:
    return RealTools(bluesky=bluesky, files=Files(store, clock))

@pytest.fixture
def brain(store, tools, clock) -> Gated:
    return Gated(store, tools=tools, clock=clock)

@pytest.fixture
def flows(brain, chat, store, clock) -> Flows:
    return Flows(brain, chat, store=store, debounce=0, typing_every=60, clock=clock)

def approval_of(flows: Flows, card) -> str:
    return next(a for a, c in flows._state.approvals.items() if c.message_id == card.message_id)

def business_of(flows: Flows) -> str:
    return next(iter(flows._state.businesses.values()))

def cards(chat: FakeChat, thread_id: int) -> list:
    return [m for m in chat.thread(thread_id) if "Draft for approval" in m.text]

async def test_an_edit_left_open_times_out_and_the_next_message_is_a_request(
    flows, chat, clock, bluesky
):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Edit")
    clock.now = NOW + timedelta(minutes=11)

    await say(flows, "Write a post about our Saturday workshop", thread_id)

    assert ui.EDIT_TIMED_OUT in post.text and post.data("Approve")
    assert bluesky.posts == []
    assert "Write a post about our Saturday workshop" in flows._brain.seen

async def test_the_tick_ends_a_forgotten_reason_prompt(flows, chat, clock):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Reject")
    await tap(flows, chat.messages[post.message_id], "Give a reason")
    assert post.data("Cancel")

    clock.now = NOW + timedelta(minutes=11)
    await flows.tick()

    assert ui.REASON_TIMED_OUT in post.text and post.data("Reject")
    assert flows._state.pending == {}

async def test_cancel_on_a_reason_prompt_puts_the_draft_back(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Reject")
    await tap(flows, chat.messages[post.message_id], "Give a reason")

    assert await tap(flows, post, "Cancel") == ui.CANCELLED

    assert post.data("Approve") and flows._state.pending == {}

async def test_a_second_prompt_puts_the_first_draft_back(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, email = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Reject")
    await tap(flows, chat.messages[post.message_id], "Give a reason")

    await tap(flows, email, "Reject")
    await tap(flows, chat.messages[email.message_id], "Give a reason")

    assert post.data("Approve")
    (pending,) = flows._state.pending.values()
    assert pending.kind == "reason"
    assert await tap(flows, post, "Approve") is None

async def test_a_failed_post_comes_back_with_try_again(store, chat, clock):
    tools = RealTools(files=Files(store, clock))
    brain = Gated(store, tools=tools, clock=clock)
    flows = Flows(brain, chat, store=store, debounce=0, clock=clock)
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)

    await tap(flows, post, "Approve")

    assert "Bluesky isn't connected" in post.text and "Nothing went out." in post.text
    assert post.data("Try again") and post.data("Edit")
    bluesky = FakeBluesky()
    tools._bluesky = bluesky
    await tap(flows, post, "Try again")
    assert bluesky.posts and chat.find("Posted to Bluesky")

async def test_a_failed_post_on_the_dashboard_says_why_and_stays_pending(store, chat, clock):
    tools = RealTools(files=Files(store, clock))
    brain = Gated(store, tools=tools, clock=clock)
    flows = Flows(brain, chat, store=store, debounce=0, clock=clock)
    thread_id = await marketing_team(flows, chat)
    await drafts(flows, chat, thread_id)
    business_id = business_of(flows)
    approval = next(
        a for a in await store.list_approvals(business_id, "pending")
        if a.task_type == "social_post"
    )

    with pytest.raises(Refused, match="Bluesky isn't connected.*back in Drafts"):
        await flows.decide_from_dashboard(business_id, approval.approval_id, "approve")

    assert (await store.get_approval(approval.approval_id)).status == "pending"
    assert approval.approval_id not in flows.deciding

async def test_a_deleted_card_doesnt_freeze_a_dashboard_decision(flows, chat, bluesky):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    approval_id = approval_of(flows, post)
    await chat.delete(CHAT, post.message_id)

    await flows.decide_from_dashboard(business_of(flows), approval_id, "approve")
    await flows.drain()

    assert bluesky.posts and approval_id not in flows.deciding

async def test_replying_to_a_draft_revises_it(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    before = len(cards(chat, thread_id))

    await flows.on_text(CHAT, thread_id, "make it shorter", 50, NOW, reply_to=post.message_id)
    await flows.drain()

    assert "↻ Revising: make it shorter" in post.text and post.keyboard == []
    assert len(cards(chat, thread_id)) == before + 1

async def test_a_newer_draft_of_the_same_task_closes_the_open_card(flows, chat, store):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    card = next(c for c in flows._state.approvals.values() if c.message_id == post.message_id)
    approval = await store.get_approval(card.approval_id)
    event = NeedsApproval(
        approval_id="a-revised",
        team_id=approval.team_id,
        task_id=approval.task_id,
        task_type=approval.task_type,
        persona=card.persona,
        preview="Shorter",
        planned_action=approval.planned_action,
        check_confidence=0.9,
    )

    await flows._render_approval(CHAT, business_of(flows), event)

    assert post.text.endswith(f"<i>{ui.REVISED_BELOW}</i>") and post.keyboard == []

async def test_approve_does_not_wait_for_the_team(flows, chat, brain, bluesky):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    brain.gate.clear()
    await flows.on_text(CHAT, thread_id, "Now a post about Saturday", 60, NOW)
    await asyncio.sleep(0.01)

    await flows.on_callback(CHAT, post.message_id, post.data("Approve"))
    for _ in range(50):
        if bluesky.posts:
            break
        await asyncio.sleep(0.01)

    assert bluesky.posts
    brain.gate.set()
    await flows.drain()

async def test_a_message_for_a_busy_team_is_marked_queued(flows, chat, brain):
    thread_id = await marketing_team(flows, chat)
    brain.gate.clear()
    await flows.on_text(CHAT, thread_id, REQUEST, 60, NOW)
    await asyncio.sleep(0.01)

    await flows.on_text(CHAT, thread_id, "Also mention the free coffee", 61, NOW)
    await asyncio.sleep(0.01)

    queued = chat.find("Queued")
    assert "Maya gets to this after the current draft" in queued.text
    brain.gate.set()
    await flows.drain()
    assert queued.message_id in chat.deleted

async def test_stop_ends_the_teams_run(flows, chat, brain, store):
    thread_id = await marketing_team(flows, chat)
    brain.gate.clear()
    await flows.on_text(CHAT, thread_id, REQUEST, 60, NOW)
    await asyncio.sleep(0.01)

    await flows.on_stop(CHAT, thread_id)
    await flows.drain()

    assert chat.last(thread_id).text == ui.stopped("Maya")
    assert ui.BROKEN not in [m.text for m in chat.thread(thread_id)]
    await flows.on_stop(CHAT, thread_id)
    assert chat.last(thread_id).text == ui.NOTHING_RUNNING

async def test_a_stuck_run_is_cut_off(store, chat, clock):
    brain = Gated(store, clock=clock)
    flows = Flows(brain, chat, store=store, debounce=0, clock=clock, run_limit=0.05)
    thread_id = await marketing_team(flows, chat)
    brain.gate.clear()

    await say(flows, REQUEST, thread_id)

    assert chat.last(thread_id).text == ui.TOO_LONG_RUN
    for task in await store.list_tasks(business_of(flows)):
        assert task.status not in (TaskStatus.PLANNED, TaskStatus.IN_PROGRESS)

async def test_start_needs_rights_to_manage_topics(flows, chat):
    chat.manages_topics = False

    await flows.on_start(CHAT, None, is_forum=True)

    assert chat.last(None).text == ui.NEEDS_ADMIN
    assert flows._state.businesses == {}

async def test_hiring_waits_for_the_business_setup(flows, chat):
    await flows.on_start(CHAT, None, is_forum=True)
    await flows.drain()

    await flows.on_hire(CHAT, None, "marketing")

    assert chat.last(None).text == ui.SETUP_FIRST
    assert "Marketing" not in chat.topics

async def test_a_deleted_topic_is_opened_again(flows, chat):
    thread_id = await marketing_team(flows, chat)
    chat.gone.add(thread_id)

    await say(flows, REQUEST, thread_id)

    new_thread = chat.topics["Marketing"]
    assert new_thread != thread_id
    assert cards(chat, new_thread)
    assert "topic was deleted, so I opened a new one" in chat.last(None).text

async def test_a_long_reply_is_split(flows, chat):
    await setup_business(flows)
    long = Say(team_id=None, persona=flows._brain.list_templates()[0].personas[0], text="x " * 3000)

    await flows._render(flows_run(flows), long)

    parts = [m for m in chat.thread(None) if "x x" in m.text]
    assert len(parts) == 2 and all(len(p.text) <= ui.MESSAGE_LIMIT for p in parts)

def flows_run(flows: Flows):
    from app.chat.flows import _Run

    return _Run(CHAT, None)

async def test_forgetting_another_business_lesson_card_is_refused(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Edit")
    await say(flows, "Launching Friday, come along.", thread_id)
    await tap(flows, chat.find("Your version"), "Approve my version")
    lesson_id = next(iter(flows._state.lessons))

    with pytest.raises(Refused):
        await flows.forget_lesson("another-business", lesson_id)

    assert lesson_id in flows._state.lessons

async def timed_drafts(flows: Flows, chat: FakeChat) -> tuple[int, object]:
    thread_id = await marketing_team(flows, chat)
    await say(flows, TIMED, thread_id)
    return thread_id, cards(chat, thread_id)[-2]

async def test_a_draft_with_a_send_time_waits_for_it(flows, chat, clock, bluesky):
    thread_id, post = await timed_drafts(flows, chat)
    assert "⏰ Goes out Fri 9 Oct, 18:00 (Los Angeles time) once you approve" in post.text
    assert post.data("Change time") and post.data("Send now")

    await tap(flows, post, "Approve")

    assert "✓ Approved · ⏰ goes out Fri 9 Oct, 18:00" in post.text
    assert post.data("Cancel schedule") and bluesky.posts == []
    await flows.tick()
    assert bluesky.posts == []
    clock.now = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)
    await flows.tick()
    await flows.drain()
    assert len(bluesky.posts) == 1 and ui.SENDING_NOW in post.text

async def test_send_now_and_cancel_schedule(flows, chat, bluesky):
    _, post = await timed_drafts(flows, chat)
    await tap(flows, post, "Approve")

    assert await tap(flows, post, "Cancel schedule") == ui.SCHEDULE_CANCELLED
    assert post.data("Approve") and "Goes out" in post.text
    await tap(flows, post, "Send now")
    assert len(bluesky.posts) == 1

async def test_schedule_a_draft_by_hand(flows, chat, bluesky):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Schedule")
    assert "When should it go out? (Los Angeles time)" in post.text

    await tap(flows, post, "Other time")
    await say(flows, "whenever", thread_id)
    assert chat.last(thread_id).text == ui.BAD_TIME
    await say(flows, "sat 10am", thread_id)

    assert "Goes out Sat 10 Oct, 10:00" in post.text and post.data("Send now")
    await tap(flows, post, "Change time")
    await tap(flows, post, "Tomorrow 9:00")
    assert "Goes out Fri 9 Oct, 9:00" in post.text
    assert bluesky.posts == []

async def test_dashboard_send_times(flows, chat, clock, bluesky):
    _, post = await timed_drafts(flows, chat)
    business_id = business_of(flows)
    approval_id = approval_of(flows, post)

    with pytest.raises(Refused, match="passed"):
        await flows.set_send_time(business_id, approval_id, NOW - timedelta(hours=1))
    await flows.decide_from_dashboard(business_id, approval_id, "approve")
    assert flows._state.timed[approval_id].decision is not None and bluesky.posts == []
    await flows.cancel_scheduled(business_id, approval_id)
    await flows.set_send_time(business_id, approval_id, None)
    assert "Goes out" not in post.text and post.data("Schedule")
    await flows.decide_from_dashboard(business_id, approval_id, "approve")
    assert len(bluesky.posts) == 1
