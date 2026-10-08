from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.chat import ui
from app.chat.flows import Flows
from app.chat.port import Keyboard
from app.fake_brain import FakeBrain
from app.store.memory import InMemoryStore
from contract import IncomingMessage

CHAT = -1001234567890
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
REQUEST = "We launch our new coaching package on Friday, get the word out"

@dataclass
class Sent:
    message_id: int
    thread_id: int | None
    text: str
    keyboard: Keyboard = field(default_factory=list)

    def data(self, label: str) -> str:
        for row in self.keyboard:
            for button in row:
                if label in button.text:
                    return button.data
        raise AssertionError(f"No button {label!r} in {self.keyboard}")

class FakeChat:
    def __init__(self) -> None:
        self.messages: dict[int, Sent] = {}
        self.deleted: list[int] = []
        self.topics: dict[str, int] = {}
        self.pinned: list[int] = []
        self.typing_calls = 0

    async def send(self, chat_id, thread_id, text, keyboard=None) -> int:
        message_id = len(self.messages) + len(self.deleted) + 1
        self.messages[message_id] = Sent(message_id, thread_id, text, keyboard or [])
        return message_id

    async def edit(self, chat_id, message_id, text, keyboard=None) -> None:
        self.messages[message_id].text = text
        self.messages[message_id].keyboard = keyboard or []

    async def delete(self, chat_id, message_id) -> None:
        del self.messages[message_id]
        self.deleted.append(message_id)

    async def create_topic(self, chat_id, name) -> int:
        self.topics[name] = 100 + len(self.topics)
        return self.topics[name]

    async def pin(self, chat_id, message_id) -> None:
        self.pinned.append(message_id)

    async def typing(self, chat_id, thread_id) -> None:
        self.typing_calls += 1

    def thread(self, thread_id: int | None) -> list[Sent]:
        return [m for m in self.messages.values() if m.thread_id == thread_id]

    def last(self, thread_id: int | None) -> Sent:
        return self.thread(thread_id)[-1]

    def find(self, part: str) -> Sent:
        matches = [m for m in self.messages.values() if part in m.text]
        assert matches, f"No message containing {part!r}"
        return matches[-1]

class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def brain(clock: Clock) -> FakeBrain:
    return FakeBrain(clock=clock)

@pytest.fixture
def flows(brain: FakeBrain, chat: FakeChat, clock: Clock) -> Flows:
    return Flows(brain, chat, debounce=0, typing_every=60, clock=clock)

async def say(flows: Flows, text: str, thread_id: int | None = None, message_id: int = 1) -> None:
    await flows.on_text(CHAT, thread_id, text, message_id, NOW)
    await flows.drain()

async def tap(flows: Flows, message: Sent, label: str) -> str | None:
    toast = await flows.on_callback(CHAT, message.message_id, message.data(label))
    await flows.drain()
    return toast

async def setup_business(flows: Flows) -> None:
    await flows.on_start(CHAT, None, is_forum=True)
    await flows.drain()
    for answer in ["Bright Coaching", "Career coaching", "Mid-career engineers"]:
        await say(flows, answer)

async def marketing_team(flows: Flows, chat: FakeChat) -> int:
    await setup_business(flows)
    await tap(flows, chat.find("Setup done"), "Marketing")
    thread_id = chat.topics["Marketing"]
    for answer in ["Bluesky", "Friday launch", "list@example.com"]:
        await say(flows, answer, thread_id)
    return thread_id

async def drafts(flows: Flows, chat: FakeChat, thread_id: int) -> tuple[Sent, Sent]:
    await say(flows, REQUEST, thread_id)
    cards = [m for m in chat.thread(thread_id) if "Draft for approval" in m.text]
    return cards[-2], cards[-1]

async def test_start_needs_a_forum_group(flows, chat):
    await flows.on_start(CHAT, None, is_forum=False)
    assert chat.last(None).text == ui.NEEDS_TOPICS

async def test_messages_before_start_get_a_hint(flows, chat):
    await say(flows, "hello")
    assert chat.last(None).text == ui.NOT_SET_UP

async def test_business_onboarding_ends_with_hire_buttons(flows, chat):
    await setup_business(flows)
    card = chat.last(None)
    assert card.text == ui.SETUP_DONE
    assert card.data("Marketing") == "hi:marketing"
    assert "<b>Alex</b> <i>· Chief of staff</i>" in chat.find("What do you sell").text

async def test_hiring_creates_topic_roster_and_link(flows, chat):
    await setup_business(flows)
    toast = await tap(flows, chat.find("Setup done"), "Marketing")
    assert toast is None
    thread_id = chat.topics["Marketing"]
    roster = chat.thread(thread_id)[0]
    assert "Your Marketing team" in roster.text
    assert chat.pinned == [roster.message_id]
    link = chat.find("Marketing team hired")
    assert link.thread_id is None
    assert link.keyboard[0][0].url == f"https://t.me/c/1234567890/{thread_id}"
    question = chat.last(thread_id)
    assert "Which channels" in question.text
    assert question.data("Bluesky").startswith("qr:")

async def test_quick_reply_shows_answer_and_continues(flows, chat):
    await setup_business(flows)
    await tap(flows, chat.find("Setup done"), "Marketing")
    thread_id = chat.topics["Marketing"]
    question = chat.last(thread_id)
    await tap(flows, question, "Bluesky and LinkedIn")
    assert question.text.endswith("<i>→ Bluesky and LinkedIn</i>")
    assert question.keyboard == []
    assert "Any launch" in chat.last(thread_id).text

async def test_tapping_a_handled_message_gets_a_toast(flows, chat):
    await setup_business(flows)
    card = chat.find("Setup done")
    data = card.data("Marketing")
    await tap(flows, card, "Marketing")
    assert await flows.on_callback(CHAT, card.message_id, data) == ui.ALREADY_HANDLED

async def test_request_renders_two_drafts_and_clears_status(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, email = await drafts(flows, chat, thread_id)
    assert "Bluesky post" in post.text and "check 90%" in post.text
    assert post.data("Approve").startswith("ap:")
    assert "Email to newsletter@example.com" in email.text
    assert not any("is writing" in m.text for m in chat.messages.values())
    assert chat.deleted
    assert chat.typing_calls > 0

async def test_approve_posts_and_undo_strikes_through(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Approve")
    assert post.text.endswith(f"<i>{ui.APPROVED}</i>")
    assert post.keyboard == []
    done = chat.find("Posted to Bluesky")
    assert "View</a>" in done.text
    await tap(flows, done, "Undo")
    assert done.text.startswith("<s>Posted to Bluesky</s>")
    assert done.keyboard == []

async def test_late_undo_gets_a_toast_and_loses_its_button(flows, chat, clock):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Approve")
    done = chat.find("Posted to Bluesky")
    clock.now = NOW + timedelta(minutes=11)
    assert await tap(flows, done, "Undo") == ui.UNDO_CLOSED
    assert done.keyboard == []
    assert "<s>" not in done.text

async def test_email_has_no_undo(flows, chat):
    thread_id = await marketing_team(flows, chat)
    _, email = await drafts(flows, chat, thread_id)
    await tap(flows, email, "Approve")
    assert chat.find("Sent the email").keyboard == []

async def test_edit_checks_length_then_posts_and_learns(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Edit")
    assert "Send your version" in chat.last(thread_id).text
    await say(flows, "x" * 301, thread_id)
    assert "301 characters" in chat.last(thread_id).text
    await say(flows, "Launching Friday, come along.", thread_id)
    assert post.text.endswith(f"<i>{ui.EDITED}</i>")
    assert chat.find("Posted to Bluesky")
    lesson = chat.find("Learned:")
    assert "Marketing team only" in lesson.text
    await tap(flows, lesson, "Forget")
    assert "Forgotten" in lesson.text and lesson.keyboard == []

async def test_cancel_restores_the_draft(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    original = post.text
    await tap(flows, post, "Edit")
    await flows.on_cancel(CHAT, thread_id)
    assert post.text == original
    assert post.data("Approve")
    assert chat.last(thread_id).text == ui.CANCELLED
    await tap(flows, post, "Approve")
    assert chat.find("Posted to Bluesky")

async def test_reject_with_reason_learns_and_revises(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Reject")
    assert post.data("Give a reason").startswith("rr:")
    await tap(flows, post, "Give a reason")
    assert "What should Maya change?" in chat.last(thread_id).text
    await say(flows, "Too salesy", thread_id)
    assert post.text.endswith(f"<i>{ui.REJECTED}</i>")
    assert "Too salesy" in chat.find("Learned:").text
    revised = chat.last(thread_id)
    assert "Draft for approval" in revised.text and revised.message_id != post.message_id

async def test_reject_and_drop(flows, chat):
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Reject")
    await tap(flows, post, "Just drop it")
    assert "Okay, dropped it." in chat.last(thread_id).text

async def test_promotion_then_acting_alone(chat, clock):
    flows = Flows(FakeBrain(promotion_streak=1, clock=clock), chat, debounce=0, clock=clock)
    thread_id = await marketing_team(flows, chat)
    post, _ = await drafts(flows, chat, thread_id)
    await tap(flows, post, "Approve")
    offer = chat.find("Can I take the next step?")
    assert "<b>Next:</b> I act, then tell you" in offer.text
    await tap(flows, offer, "Yes, go ahead")
    assert "✓ Bluesky post: i act, then tell you" in offer.text
    await say(flows, REQUEST, thread_id)
    assert "Done without asking" in chat.find("Posted to Bluesky").text

async def test_debounce_joins_quick_messages(chat, clock):
    seen: list[IncomingMessage] = []

    class Recording(FakeBrain):
        def handle_message(self, msg):
            seen.append(msg)
            return super().handle_message(msg)

    flows = Flows(Recording(clock=clock), chat, debounce=0.05, clock=clock)
    await flows.on_start(CHAT, None, is_forum=True)
    await flows.drain()
    await flows.on_text(CHAT, None, "Bright", 10, NOW)
    await flows.on_text(CHAT, None, "Coaching", 11, NOW)
    await flows.drain()
    assert [m.text for m in seen] == ["Bright\nCoaching"]
    assert seen[0].message_id == "11"

async def test_unknown_topic_and_unknown_team(flows, chat):
    await setup_business(flows)
    await say(flows, "hello", thread_id=999)
    assert chat.last(999).text == ui.NOT_A_TEAM
    await flows.on_hire(CHAT, None, "sales")
    await flows.drain()
    assert chat.last(None).text.startswith("⚠️ There's no 'sales' team")

async def test_hire_without_a_name_shows_the_picker(flows, chat):
    await setup_business(flows)
    await flows.on_hire(CHAT, None, None)
    assert chat.last(None).text == ui.PICK_TEAM

async def test_cancel_with_nothing_pending(flows, chat):
    await setup_business(flows)
    await flows.on_cancel(CHAT, None)
    assert chat.last(None).text == ui.NOTHING_TO_CANCEL

async def test_one_failed_message_does_not_drop_the_rest(flows, chat):
    thread_id = await marketing_team(flows, chat)
    send = chat.send
    calls = 0

    async def flaky(chat_id, thread_id, text, keyboard=None):
        nonlocal calls
        calls += 1
        if "Bluesky post" in text and calls < 10:
            raise RuntimeError("Telegram is down")
        return await send(chat_id, thread_id, text, keyboard)

    chat.send = flaky
    await say(flows, REQUEST, thread_id)
    assert "Email to newsletter@example.com" in chat.find("Draft for approval").text
    assert chat.last(thread_id).text == ui.BROKEN

async def test_restart_restores_businesses_and_topics(chat, clock):
    store = InMemoryStore()
    first = Flows(FakeBrain(clock=clock), chat, store=store, debounce=0, clock=clock)
    thread_id = await marketing_team(first, chat)

    restarted = Flows(FakeBrain(clock=clock), chat, store=store, debounce=0, clock=clock)
    await restarted.load()
    await restarted.on_start(CHAT, None, is_forum=True)
    await restarted.drain()
    assert list((await store.list_businesses()).values()) == [first._state.businesses[CHAT]]
    await say(restarted, "hello there my friend", thread_id)
    assert chat.last(thread_id).text != ui.NOT_A_TEAM
