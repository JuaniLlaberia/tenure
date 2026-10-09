from datetime import timedelta

import pytest
from tests.app.fakes import NOW, Clock, FakeChat
from tests.app.test_flows import CHAT, REQUEST, drafts, marketing_team, say, setup_business, tap

from app.chat import reports
from app.chat.flows import Flows
from app.fake_brain import FakeBrain
from app.store.memory import InMemoryStore
from contract import AutonomyLevel, ModelUsage

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()

@pytest.fixture
def flows(store, chat, clock) -> Flows:
    brain = FakeBrain(store=store, clock=clock)
    return Flows(brain, chat, store=store, debounce=0, typing_every=60, clock=clock)

def business(flows) -> str:
    return flows._state.businesses[CHAT]

async def command(flows, chat, kind, thread_id=None):
    await flows.on_report(CHAT, thread_id, kind)
    return chat.last(thread_id)

async def test_drafts_lists_waiting_drafts_with_links(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    listed = await command(flows, chat, "drafts", thread_id)
    assert "2 drafts wait for you" in listed.text
    urls = [b.url for row in listed.keyboard for b in row]
    assert f"https://t.me/c/1234567890/{thread_id}/{post.message_id}" in urls

    await tap(flows, post, "Approve")
    again = await command(flows, chat, "drafts", thread_id)
    assert "1 draft waits for you" in again.text

async def test_drafts_when_nothing_waits(flows, chat):
    await setup_business(flows)
    assert (await command(flows, chat, "drafts")).text == reports.NOTHING_WAITS

async def test_team_lowers_trust_after_confirming(flows, chat, store):
    thread_id = await marketing_team(flows, chat)
    card = await command(flows, chat, "team", thread_id)
    assert "<b>Bluesky post:</b> I ask before acting · 0/5 clean approvals" in card.text

    await tap(flows, card, "Lower trust: Bluesky post")
    confirm = chat.messages[card.message_id]
    assert "go from “I ask before acting” back to “I only draft”" in confirm.text
    await tap(flows, confirm, "Cancel")
    assert chat.messages[card.message_id].text == card.text

    await tap(flows, chat.messages[card.message_id], "Lower trust: Bluesky post")
    toast = await tap(flows, chat.messages[card.message_id], "Yes, lower it")
    assert toast.startswith("Lowered")
    team_id = flows._state.topics[(CHAT, thread_id)]
    assert (await store.get_trust(team_id, "social_post")).level == AutonomyLevel.DRAFT_ONLY
    assert "<b>Bluesky post:</b> I only draft" in chat.messages[card.message_id].text

async def test_team_threshold_buttons(flows, chat, store):
    thread_id = await marketing_team(flows, chat)
    card = await command(flows, chat, "team", thread_id)
    await tap(flows, card, "+")
    team_id = flows._state.topics[(CHAT, thread_id)]
    assert {t.promote_after for t in await store.list_trust(team_id)} == {6}
    assert "after 6 clean approvals" in chat.messages[card.message_id].text
    assert await tap(flows, chat.messages[card.message_id], "Offer after 6") is None

async def test_team_in_general_shows_every_team(flows, chat):
    await setup_business(flows)
    assert (await command(flows, chat, "team")).text == reports.NO_TEAMS
    await marketing_team_from_general(flows, chat)
    await command(flows, chat, "team")
    assert "<b>Marketing</b> · Maya, Leo, Sam" in chat.last(None).text

async def marketing_team_from_general(flows, chat):
    await tap(flows, chat.find("Setup done"), "Marketing")

async def test_knowledge_pages_and_forgets(flows, chat, store):
    thread_id = await marketing_team(flows, chat)
    for n in range(4):
        await say(flows, f"Stop using emoji type {n}", thread_id)
    listed = await command(flows, chat, "knowledge", thread_id)
    assert "<b>What Marketing knows about you</b> · 7 lessons" in listed.text
    assert "Next →" in [b.text for row in listed.keyboard for b in row]
    await tap(flows, listed, "Next →")
    assert "6. " in chat.messages[listed.message_id].text

    await tap(flows, chat.messages[listed.message_id], "← Back")
    first = chat.messages[listed.message_id]
    await tap(flows, first, "Forget 1")
    lessons = await store.list_all_lessons(business(flows))
    assert len(lessons) == 6
    assert "6 lessons" in chat.messages[listed.message_id].text

async def test_activity_undoes_a_post_from_the_list(flows, chat, store, clock):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Approve")
    listed = await command(flows, chat, "activity", thread_id)
    assert "Posted to Bluesky" in listed.text
    assert "1 done · 1 waiting for you · 0 failed" in listed.text
    undo = next(b.text for row in listed.keyboard for b in row if b.text.startswith("↩ Undo"))
    assert "(10 min)" in undo
    await tap(flows, listed, "↩ Undo")
    entries = await store.list_actions(business(flows))
    assert any(e.undone_at for e in entries)
    assert "undone" in chat.messages[listed.message_id].text

async def test_activity_hides_undo_after_the_window(flows, chat, clock):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Approve")
    clock.now = NOW + timedelta(minutes=11)
    listed = await command(flows, chat, "activity", thread_id)
    assert not any(b.text.startswith("↩") for row in listed.keyboard for b in row)

async def test_spend_sums_billed_cost_per_model(flows, chat, store):
    await setup_business(flows)
    for cost in (0.25, 0.5):
        await store.log_usage(ModelUsage(
            usage_id=f"u{cost}", business_id=business(flows), model="anthropic/claude-sonnet-5.5",
            purpose="Reviews", input_tokens=1000, output_tokens=200, cost=cost, at=NOW,
        ))
    listed = await command(flows, chat, "spend")
    assert "$0.75 billed · 2,400 tokens" in listed.text
    assert "Reviews · 2 calls · $0.75" in listed.text

async def test_schedules_list_runs_and_stops(flows, chat, store):
    thread_id = await marketing_team(flows, chat)
    await say(flows, "Every Monday, research a topic and propose a newsletter", thread_id)
    listed = await command(flows, chat, "schedules")
    assert "Every Monday at 9:00 (Los Angeles)" in listed.text and "Marketing" in listed.text
    await tap(flows, listed, "■ Stop")
    assert "stopped" in chat.messages[listed.message_id].text
    (schedule,) = await store.list_schedules(business(flows))
    assert not schedule.active
    await tap(flows, chat.messages[listed.message_id], "↻ Turn on")
    assert (await store.get_schedule(schedule.schedule_id)).active

async def test_no_schedules_explains_how(flows, chat):
    await setup_business(flows)
    assert (await command(flows, chat, "schedules")).text == reports.NO_SCHEDULES

async def test_request_still_works_after_a_command(flows, chat):
    thread_id = await marketing_team(flows, chat)
    await command(flows, chat, "drafts", thread_id)
    await say(flows, REQUEST, thread_id)
    assert [m for m in chat.thread(thread_id) if "Draft for approval" in m.text]
