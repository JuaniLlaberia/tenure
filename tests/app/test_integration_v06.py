"""
Contract v0.6 end to end: the app's Telegram flows, file storage and real tools driving Juan's
brain, with scripted models. Bluesky and email are recorded instead of sent.
"""

import base64
import re

import pytest
from tests.app.fakes import NOW, Clock, FakeChat
from tests.app.test_flows import CHAT, say, tap

from app.chat.flows import Flows
from app.chat.port import IncomingFile
from app.files import Files
from app.scheduler import Scheduler
from app.store.memory import InMemoryStore
from app.tools.real import RealTools
from brain.brain import TenureBrain
from brain.deps import Deps, Settings
from brain.fakes import PNG, FakeImages, FakeJev, FakeLLM
from brain.helpers.llm import Completion, ToolCall
from brain.templates.loader import load_templates
from contract import FileKind

PROFILE = {
    "name": "Bright Coaching",
    "what_you_sell": "Career coaching",
    "customers": "Mid-career engineers",
    "tone": "warm",
}
TRANSCRIPT = "We launch Friday, get the word out"
ALT = "A paper rocket over a green field"
WEEKLY = "Every Monday at 9, research a coaching topic and propose a newsletter"

class Bluesky:
    def __init__(self) -> None:
        self.posts: list[tuple[str, list]] = []

    async def post(self, text, images=None):
        self.posts.append((text, images or []))
        n = len(self.posts)
        return f"at://did:plc:x/app.bsky.feed.post/{n}", f"https://bsky.app/profile/x/post/{n}"

    async def delete(self, uri):
        pass

class Email:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, list]] = []

    async def send(self, to, subject, body, images=None):
        self.sent.append((to, subject, body, images or []))
        return "em_1"

def system_of(messages) -> str:
    return str(messages[0].get("content", "")) if messages else ""

def has_audio(messages) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(part.get("type") == "input_audio" for part in m["content"])
        for m in messages
    )

def complete(messages) -> Completion:
    """
    Transcribes voice notes; Otto makes one image per run; everyone else just finishes.
    """
    if has_audio(messages):
        return Completion(text=TRANSCRIPT)
    otto = "You are Otto" in system_of(messages)
    if otto and not any(m.get("role") == "tool" for m in messages):
        arguments = {"prompt": "A paper rocket", "aspect_ratio": "1:1", "alt_text": ALT}
        call = ToolCall(id="c1", name="generate_image", arguments=arguments)
        return Completion(text="", tool_calls=[call])
    return Completion(text="Done.")

def plan(messages) -> dict:
    if "Iris" in system_of(messages):
        task = {"task_type": "visual", "title": "Launch visual", "brief": "A launch image"}
    else:
        task = {"task_type": "social_post", "title": "Launch post", "brief": "Friday launch"}
    return {"tasks": [task], "question": None}

def made_images(messages) -> dict:
    replies = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "tool")
    return {"images": re.findall(r"Saved image (\S+):", replies), "caption": "For the launch"}

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def bluesky() -> Bluesky:
    return Bluesky()

@pytest.fixture
def email() -> Email:
    return Email()

@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM(
        {
            "Extraction": {"fields": PROFILE, "facts": []},
            "LeadPlan": plan,
            "SocialPostOutput": {"text": "We launch Friday!", "images": []},
            "ImageOutput": made_images,
            "CriticReport": {"issues": []},
            "ReflectOutput": {"lessons": []},
            "ScheduleDraft": {
                "title": "Monday newsletter idea",
                "request": "Research a coaching topic and propose a newsletter",
                "every": "week",
                "weekday": 0,
                "hour": 9,
                "minute": 0,
            },
        },
        completions=complete,
    )

@pytest.fixture
def images() -> FakeImages:
    return FakeImages()

@pytest.fixture
def flows(store, clock, bluesky, email, llm, images) -> Flows:
    jev = FakeJev(
        {
            "answer_clear": 0.9,
            "has_feedback": 0.1,
            "is_clear": 0.9,
            "passes_check": 0.9,
            "names_channels": 0.9,
            "needs_reasoning": 0.1,
            "about_image": 0.9,
            "changes_schedule": 0.1,
            "wants_schedule": lambda state: 0.9 if "Every Monday" in state else 0.1,
            "needs_social_post": 0.9,
            "needs_newsletter": 0.1,
            "needs_competitor_check": 0.1,
            "needs_visual": 0.9,
        }
    )
    tools = RealTools(bluesky=bluesky, email=email, files=Files(store, clock))
    deps = Deps(
        store=store,
        tools=tools,
        llm=llm,
        jev=jev,
        settings=Settings(),
        templates=load_templates(),
        clock=clock,
        images=images,
    )
    brain = TenureBrain(deps)
    return Flows(brain, FakeChat(), store=store, debounce=0, typing_every=60, clock=clock)

def chat_of(flows: Flows) -> FakeChat:
    return flows._chat

async def hire(flows: Flows, template: str, answers: list[str]) -> int:
    await flows.on_hire(CHAT, None, template)
    await flows.drain()
    chat = chat_of(flows)
    name = {"marketing": "Marketing", "design": "Design"}[template]
    thread_id = chat.topics[name]
    for answer in answers:
        await say(flows, answer, thread_id)
    return thread_id

async def business(flows: Flows) -> int:
    await flows.on_start(CHAT, None, is_forum=True)
    await flows.drain()
    await say(flows, "We're Bright Coaching: career coaching for mid-career engineers, warm tone")
    answers = ["Bluesky and newsletter", "We launch Friday", "list@b.co"]
    return await hire(flows, "marketing", answers)

def cards(chat: FakeChat, thread_id: int) -> list:
    return [m for m in chat.thread(thread_id) if "Draft for approval" in m.text]

async def test_a_voice_note_is_transcribed_and_drafted(flows, llm):
    thread_id = await business(flows)
    chat = chat_of(flows)
    chat.files["voice"] = b"OggS voice note"

    await flows.on_file(CHAT, thread_id, "", IncomingFile("voice", "audio/ogg", None, 15), 70, NOW)
    await flows.drain()

    (card,) = cards(chat, thread_id)
    assert "We launch Friday!" in card.text
    audio = [
        part["input_audio"]
        for call in llm.calls
        for m in call.messages
        if isinstance(m.get("content"), list)
        for part in m["content"]
        if part.get("type") == "input_audio"
    ]
    assert audio and base64.b64decode(audio[0]["data"]) == b"OggS voice note"
    assert audio[0]["format"] == "ogg"

async def test_with_design_hired_the_post_carries_an_image_to_bluesky(flows, bluesky, images):
    thread_id = await business(flows)
    await hire(flows, "design", ["Green and amber", "Illustration", "Nothing in particular"])
    chat = chat_of(flows)

    await say(flows, "Post about our Friday launch", thread_id)

    (card,) = cards(chat, thread_id)
    assert card.photos == [PNG] and "1 image" in card.text
    assert images.calls
    await tap(flows, card, "Approve")
    ((text, sent),) = bluesky.posts
    assert text == "We launch Friday!"
    assert [(i.data, i.alt) for i in sent] == [(PNG, ALT)]

async def test_removing_the_image_in_telegram_posts_text_only(flows, bluesky):
    thread_id = await business(flows)
    await hire(flows, "design", ["Green and amber", "Illustration", "Nothing in particular"])
    chat = chat_of(flows)
    await say(flows, "Post about our Friday launch", thread_id)
    (card,) = cards(chat, thread_id)

    await tap(flows, card, "Edit")
    menu = chat.last(thread_id)
    await tap(flows, menu, "✕ Image 1")
    await tap(flows, chat.messages[menu.message_id], "Approve my version")

    ((text, sent),) = bluesky.posts
    assert text == "We launch Friday!" and sent == []

async def test_a_design_visual_is_a_photo_draft_without_edit(flows, store):
    await business(flows)
    design = await hire(flows, "design", ["Green and amber", "Illustration", "Nothing"])
    chat = chat_of(flows)

    await say(flows, "Make an image for our Friday launch", design)

    (card,) = cards(chat, design)
    assert card.photos == [PNG]
    labels = [b.text for row in card.keyboard for b in row]
    assert "✎ Edit" not in labels and "✓ Approve" in labels
    business_id = next(iter(flows._state.businesses.values()))
    (approval,) = await store.list_approvals(business_id, "pending")
    assert [f.kind for f in approval.media] == [FileKind.IMAGE]

async def test_a_schedule_card_runs_through_the_real_brain(flows, store, clock):
    thread_id = await business(flows)
    chat = chat_of(flows)

    await say(flows, WEEKLY, thread_id)

    card = chat.find("🔁")
    assert "Monday newsletter idea" in card.text and "Every Monday at 9:00" in card.text
    business_id = next(iter(flows._state.businesses.values()))
    (schedule,) = await store.list_schedules(business_id)
    assert schedule.next_run_at is not None

    await tap(flows, card, "Run now")
    runs = await store.list_tasks(business_id)
    tasks = [t for t in runs if t.schedule_id == schedule.schedule_id]
    assert tasks and cards(chat, thread_id)

    clock.now = schedule.next_run_at
    await Scheduler(store, flows, clock).run_due()
    await flows.drain()
    runs = [t for t in await store.list_tasks(business_id) if t.schedule_id == schedule.schedule_id]
    assert len(runs) > len(tasks)

async def test_new_image_through_the_real_brain_makes_one_image(flows, images, bluesky):
    thread_id = await business(flows)
    await hire(flows, "design", ["Green and amber", "Illustration", "Nothing in particular"])
    chat = chat_of(flows)
    await say(flows, "Post about our Friday launch", thread_id)
    (card,) = cards(chat, thread_id)
    assert len(images.calls) == 1

    await tap(flows, card, "New image")
    await tap(flows, chat.messages[card.message_id], "Just try again")

    again = cards(chat, thread_id)[-1]
    assert again.message_id != card.message_id
    assert "We launch Friday!" in again.text and again.photos == [PNG]
    assert len(images.calls) == 2
    await tap(flows, again, "Approve")
    ((_, sent),) = bluesky.posts
    assert len(sent) == 1
