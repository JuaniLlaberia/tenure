from datetime import UTC, datetime

import pytest
from tests.app.fakes import NOW, Clock, FakeBluesky, FakeChat
from tests.app.test_flows import CHAT, REQUEST, marketing_team, say, tap

from app.chat import ui
from app.chat.flows import Flows, Refused
from app.chat.port import IncomingFile
from app.fake_brain import FakeBrain
from app.files import Files
from app.scheduler import Scheduler, next_run
from app.store.memory import InMemoryStore
from app.tools.real import RealTools
from contract import ApprovalDecision, Cadence, SendEmail

class Recording(FakeBrain):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.decisions: list[ApprovalDecision] = []
        self.runs: list[str] = []

    def resolve_approval(self, decision):
        self.decisions.append(decision)
        return super().resolve_approval(decision)

    def run_schedule(self, business_id, schedule_id):
        self.runs.append(schedule_id)
        return super().run_schedule(business_id, schedule_id)

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def clock() -> Clock:
    return Clock()

def tools_for(store, clock) -> RealTools:
    return RealTools(bluesky=FakeBluesky(), files=Files(store, clock))

@pytest.fixture
def brain(store, clock) -> Recording:
    return Recording(store=store, tools=tools_for(store, clock), clock=clock)

@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()

@pytest.fixture
def flows(brain, chat, store, clock) -> Flows:
    return Flows(brain, chat, store=store, debounce=0, typing_every=60, clock=clock)

async def image_drafts(flows, chat):
    thread_id = await marketing_team(flows, chat)
    await say(flows, f"{REQUEST}, with an image", thread_id)
    cards = [m for m in chat.thread(thread_id) if "Draft for approval" in m.text]
    return thread_id, cards[-2], cards[-1]

async def test_one_image_draft_is_a_photo_with_the_card_as_caption(flows, chat):
    _, post, email = await image_drafts(flows, chat)
    assert len(post.photos) == 1 and post.photos[0].startswith(b"\x89PNG")
    assert "1 image ·" in post.text and post.data("Approve")
    assert len(email.photos) == 1 and "1 image" in email.text

async def test_two_or_more_images_go_in_an_album_above_the_card(chat, store, clock):
    brain = Recording(store=store, tools=tools_for(store, clock), clock=clock)
    flows = Flows(brain, chat, store=store, debounce=0.05, typing_every=60, clock=clock)
    thread_id = await marketing_team(flows, chat)
    chat.files.update({"t1": b"jpg1", "t2": b"jpg2"})
    caption = "Post these with a line about the new studio"
    await flows.on_file(CHAT, thread_id, caption, IncomingFile("t1", "image/jpeg"), 50, NOW)
    await flows.on_file(CHAT, thread_id, "", IncomingFile("t2", "image/jpeg"), 51, NOW)
    await flows.drain()
    album = [m for m in chat.thread(thread_id) if len(m.photos) == 2]
    assert album and album[-1].photos == [b"jpg1", b"jpg2"]
    card = [m for m in chat.thread(thread_id) if "Draft for approval" in m.text][-2]
    assert card.photos == [] and "2 images above" in card.text and card.data("Edit")

def brain_business(flows) -> str:
    return next(iter(flows._state.businesses.values()))

async def test_email_edit_menu_changes_subject_removes_image_and_approves(flows, chat, brain):
    thread_id, _, email = await image_drafts(flows, chat)
    await tap(flows, email, "Edit")
    menu = chat.last(thread_id)
    assert "What do you want to change in the email" in menu.text
    labels = {b.text for row in menu.keyboard for b in row}
    assert labels >= {"Text", "Subject", "Recipient", "✕ Image 1"}

    await tap(flows, menu, "Subject")
    await say(flows, "Doors open this Friday at 6", thread_id)
    version = chat.messages[menu.message_id]
    assert "Your version" in version.text and "subject changed" in version.text

    await tap(flows, version, "Change more")
    await tap(flows, chat.messages[menu.message_id], "✕ Image 1")
    version = chat.messages[menu.message_id]
    assert "image 1 removed" in version.text and "0 images" in version.text

    await tap(flows, version, "Approve my version")
    (decision,) = brain.decisions
    assert decision.decision == "edit"
    assert isinstance(decision.edited_action, SendEmail)
    assert decision.edited_action.subject == "Doors open this Friday at 6"
    assert decision.edited_action.images == []
    assert decision.edited_text == decision.edited_action.body
    assert ui.APPROVED_YOURS in chat.messages[menu.message_id].text

async def test_bad_recipient_is_asked_again(flows, chat):
    thread_id, _, email = await image_drafts(flows, chat)
    await tap(flows, email, "Edit")
    await tap(flows, chat.last(thread_id), "Recipient")
    await say(flows, "not an address", thread_id)
    assert chat.last(thread_id).text == ui.BAD_ADDRESS

async def test_undo_my_changes_restores_the_draft(flows, chat, brain):
    thread_id, post, _ = await image_drafts(flows, chat)
    await tap(flows, post, "Edit")
    menu = chat.last(thread_id)
    await tap(flows, menu, "✕ Image 1")
    await tap(flows, chat.messages[menu.message_id], "Undo my changes")
    assert ui.EDIT_UNDONE in chat.messages[menu.message_id].text
    assert chat.messages[post.message_id].data("Approve")
    assert brain.decisions == []

async def test_dashboard_decision_closes_a_telegram_edit(flows, chat, brain):
    thread_id, post, _ = await image_drafts(flows, chat)
    await tap(flows, post, "Edit")
    menu = chat.last(thread_id)
    approval_id = menu.data("Cancel").split(":")[1]
    await flows.decide_from_dashboard(brain_business(flows), approval_id, "approve")
    await flows.drain()
    assert ui.APPROVED_ON_DASHBOARD in chat.messages[menu.message_id].text
    assert flows._state.edits.get(approval_id) is None

async def test_a_plain_post_edit_shows_your_version_before_posting(chat, clock):
    store = InMemoryStore()
    flows = Flows(
        FakeBrain(store, clock=clock), chat, store=store, debounce=0, typing_every=60, clock=clock
    )
    thread_id = await marketing_team(flows, chat)
    await say(flows, REQUEST, thread_id)
    post = [m for m in chat.thread(thread_id) if "Draft for approval" in m.text][-2]
    await tap(flows, post, "Edit")
    assert chat.last(thread_id).text.startswith("Send your version of the post")

    await say(flows, "Doors open Friday at 6.", thread_id)

    version = chat.last(thread_id)
    assert "Your version" in version.text and "Doors open Friday at 6." in version.text
    assert not [a for a in await store.list_actions(brain_business(flows))]
    await tap(flows, version, "Approve my version")
    (action,) = await store.list_actions(brain_business(flows))
    assert action.tool == "post_social"

async def test_schedule_card_runs_stops_and_turns_back_on(flows, chat, brain, store):
    thread_id = await marketing_team(flows, chat)
    await say(flows, "Every Monday, research a topic and propose a newsletter", thread_id)
    card = chat.find("🔁")
    assert "Every Monday at 9:00 (Los Angeles)" in card.text
    assert "Next: Mon Oct 12, 9:00" in card.text
    (schedule,) = await store.list_schedules(brain_business(flows))
    assert schedule.next_run_at == datetime(2026, 10, 12, 16, 0, tzinfo=UTC)

    assert await tap(flows, card, "Run now") is None
    assert brain.runs == [schedule.schedule_id]
    assert [m for m in chat.thread(thread_id) if "Draft for approval" in m.text]
    assert (await store.get_schedule(schedule.schedule_id)).last_run_at == NOW

    await tap(flows, chat.messages[card.message_id], "Stop")
    stopped = chat.messages[card.message_id]
    assert "<s>" in stopped.text and stopped.data("Turn back on")
    assert not (await store.get_schedule(schedule.schedule_id)).active
    with pytest.raises(Refused):
        await flows.run_schedule_now(brain_business(flows), schedule.schedule_id)

    await tap(flows, stopped, "Turn back on")
    assert (await store.get_schedule(schedule.schedule_id)).active
    assert chat.messages[card.message_id].data("Run now")

async def test_run_now_waits_while_the_team_waits_for_an_answer(flows, chat, store):
    thread_id = await marketing_team(flows, chat)
    await say(flows, "Every Monday, research a topic and propose a newsletter", thread_id)
    card = chat.find("🔁")
    await say(flows, "Post", thread_id)
    assert await tap(flows, card, "Run now") == ui.TEAM_BUSY

DAILY = Cadence(every="day", hour=8, timezone="UTC")

@pytest.mark.parametrize(
    "cadence, after, expected",
    [
        (Cadence(every="week", weekday=0), datetime(2026, 10, 9, 12, tzinfo=UTC), (10, 12, 16)),
        (Cadence(every="week", weekday=0), datetime(2026, 10, 12, 17, tzinfo=UTC), (10, 19, 16)),
        (DAILY, datetime(2026, 10, 9, tzinfo=UTC), (10, 9, 8)),
        (Cadence(every="week", weekday=0), datetime(2026, 10, 30, 12, tzinfo=UTC), (11, 2, 17)),
    ],
)
def test_next_run(cadence, after, expected):
    month, day, hour = expected
    assert next_run(cadence, after) == datetime(2026, month, day, hour, tzinfo=UTC)

def test_next_run_monthly_rolls_into_the_new_year():
    after = datetime(2026, 12, 5, tzinfo=UTC)
    assert next_run(Cadence(every="month", day=1), after) == datetime(2027, 1, 1, 17, tzinfo=UTC)

async def test_scheduler_runs_due_schedules_once_and_moves_on(flows, chat, brain, store, clock):
    thread_id = await marketing_team(flows, chat)
    await say(flows, "Every Monday, research a topic and propose a newsletter", thread_id)
    (schedule,) = await store.list_schedules(brain_business(flows))
    scheduler = Scheduler(store, flows, clock)

    await scheduler.run_due()
    assert brain.runs == []

    clock.now = datetime(2026, 10, 12, 16, 1, tzinfo=UTC)
    await scheduler.run_due()
    await flows.drain()
    assert brain.runs == [schedule.schedule_id]
    saved = await store.get_schedule(schedule.schedule_id)
    assert saved.last_run_at == clock.now
    assert saved.next_run_at == datetime(2026, 10, 19, 16, tzinfo=UTC)

    await scheduler.run_due()
    assert len(brain.runs) == 1

async def test_new_image_in_telegram_with_a_reason(flows, chat, brain):
    thread_id, post, _ = await image_drafts(flows, chat)
    assert post.data("New image")

    await tap(flows, post, "New image")
    asked = chat.messages[post.message_id]
    assert ui.NEW_IMAGE_ASK in asked.text
    await tap(flows, asked, "Say what to change")
    assert chat.last(thread_id).text == ui.IMAGE_REASON_PROMPT
    await say(flows, "Warmer colours, no people", thread_id)

    (decision,) = brain.decisions
    assert (decision.decision, decision.reason) == ("new_image", "Warmer colours, no people")
    assert "New image requested: Warmer colours" in chat.messages[post.message_id].text
    again = [m for m in chat.thread(thread_id) if "Draft for approval" in m.text][-1]
    assert again.message_id != post.message_id and len(again.photos) == 1

async def test_new_image_just_try_again(flows, chat, brain):
    _, post, _ = await image_drafts(flows, chat)
    await tap(flows, post, "New image")
    await tap(flows, chat.messages[post.message_id], "Just try again")
    (decision,) = brain.decisions
    assert (decision.decision, decision.reason) == ("new_image", None)

async def test_new_image_without_revisions_left_keeps_the_draft(flows, chat, brain, store):
    _, post, _ = await image_drafts(flows, chat)
    card = flows._state.approvals[post.data("Approve").split(":")[1]]
    task = await store.get_task(card.task_id)
    await store.save_task(task.model_copy(update={"revisions": 2}))

    assert await tap(flows, post, "New image") == ui.NO_REVISIONS_LEFT
    assert chat.messages[post.message_id].data("Approve")
    assert brain.decisions == []

async def test_founder_photos_get_no_new_image_button(chat, store, clock):
    brain = Recording(store=store, tools=tools_for(store, clock), clock=clock)
    flows = Flows(brain, chat, store=store, debounce=0.05, typing_every=60, clock=clock)
    thread_id = await marketing_team(flows, chat)
    chat.files["t1"] = b"jpg1"
    caption = "Post this with a line about the new studio"
    await flows.on_file(CHAT, thread_id, caption, IncomingFile("t1", "image/jpeg"), 50, NOW)
    await flows.drain()
    card = [m for m in chat.thread(thread_id) if "Draft for approval" in m.text][-2]
    assert "New image" not in [b.text for row in card.keyboard for b in row]
