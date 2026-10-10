"""
The app's Telegram flows driving Juan's brain, with scripted LLM answers instead of OpenRouter.
"""

import pytest
from tests.app.fakes import FakeChat
from tests.app.test_flows import CHAT, say, tap

from app.chat.flows import Flows
from app.fake_brain import FakeBrain
from app.main import open_brain, with_retries
from app.store.memory import InMemoryStore
from brain.brain import TenureBrain
from brain.deps import Deps, Settings
from brain.fakes import FakeJev, FakeLLM, FakeTools
from brain.templates.loader import load_templates

PROFILE = {
    "name": "Bright Coaching",
    "what_you_sell": "Career coaching",
    "customers": "Mid-career engineers",
    "tone": "warm",
}

@pytest.fixture
def tools() -> FakeTools:
    return FakeTools()

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def brain(store: InMemoryStore, tools: FakeTools) -> TenureBrain:
    jev = FakeJev(
        {
            "answer_clear": 0.9,
            "has_feedback": 0.1,
            "is_clear": 0.9,
            "passes_check": 0.9,
            "names_channels": 0.1,
        }
    )
    for task_type in ("social_post", "newsletter", "competitor_check"):
        jev.answers[f"needs_{task_type}"] = 0.9 if task_type == "social_post" else 0.1
    llm = FakeLLM(
        {
            "Extraction": {"fields": PROFILE, "facts": []},
            "LeadPlan": {
                "tasks": [{"task_type": "social_post", "title": "Launch post", "brief": "Friday"}],
                "question": None,
            },
            "SocialPostOutput": {"text": "We launch Friday!"},
            "CriticReport": {"issues": []},
            "ReflectOutput": {"lessons": []},
        }
    )
    deps = Deps(
        store=store, tools=tools, llm=llm, jev=jev, settings=Settings(), templates=load_templates()
    )
    return TenureBrain(deps)

async def test_onboard_hire_draft_and_post(brain: TenureBrain, store, tools: FakeTools):
    chat = FakeChat()
    flows = Flows(brain, chat, store=store, debounce=0, typing_every=60)
    await flows.on_start(CHAT, None, is_forum=True)
    await flows.drain()
    await say(flows, "Here's our site: https://bright.example")
    await tap(flows, chat.find("Setup done"), "Marketing")
    thread_id = chat.topics["Marketing"]
    await tap(flows, chat.find("Which channels"), "Bluesky and newsletter")
    for answer in ["We launch Friday", "list@b.co"]:
        await say(flows, answer, thread_id)
    await say(flows, "We launch Friday, get the word out", thread_id)
    await tap(flows, chat.find("Where should this go out?"), "Only Bluesky")
    card = chat.find("Draft for approval")
    assert "We launch Friday!" in card.text
    await tap(flows, card, "Approve")
    assert chat.find("Posted to Bluesky")
    assert [call for call, _ in tools.calls] == ["fetch_page", "post_social"]

async def test_without_an_openrouter_key_the_app_refuses_to_start(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("FAKE_BRAIN", raising=False)
    with pytest.raises(SystemExit, match="OPENROUTER_API_KEY"):
        async with open_brain(InMemoryStore(), FakeTools()):
            pass

async def test_the_fake_brain_runs_only_when_asked(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("FAKE_BRAIN", "1")
    async with open_brain(InMemoryStore(), FakeTools()) as brain:
        assert isinstance(brain, FakeBrain)

async def test_with_a_key_the_real_brain_runs(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("FAKE_BRAIN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    async with open_brain(InMemoryStore(), FakeTools()) as brain:
        assert isinstance(brain, TenureBrain)

async def test_startup_steps_are_retried_until_the_database_answers():
    tries = []

    async def load():
        tries.append(1)
        if len(tries) < 3:
            raise ConnectionError("Supabase is waking up")
        return "loaded"

    assert await with_retries("load", load, wait=0) == "loaded"
    assert len(tries) == 3

async def test_startup_gives_up_with_a_clear_message():
    async def load():
        raise ConnectionError("no route to host")

    with pytest.raises(SystemExit, match="Couldn't load after 2 tries.*no route to host"):
        await with_retries("load", load, tries=2, wait=0)
