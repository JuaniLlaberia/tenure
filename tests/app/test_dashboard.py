import httpx
import pytest
from tests.app.fakes import NOW, Clock, FakeChat

from app.chat import ui
from app.chat.flows import Flows
from app.fake_brain import FakeBrain
from app.store.memory import InMemoryStore
from app.web.api import create_api
from contract import AutonomyLevel

CHAT = -1001234567890
REQUEST = "We launch our new coaching package on Friday, get the word out"

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()

@pytest.fixture
def brain(store, clock) -> FakeBrain:
    return FakeBrain(store, clock=clock)

@pytest.fixture
def flows(brain, chat, store, clock) -> Flows:
    return Flows(brain, chat, store=store, debounce=0, clock=clock, dashboard_url="https://t.example/")

@pytest.fixture
async def client(flows, store, brain, clock):
    api = create_api(flows, store, brain, clock=clock)
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

async def say(flows: Flows, text: str, thread_id: int | None = None) -> None:
    await flows.on_text(CHAT, thread_id, text, 1, NOW)
    await flows.drain()

async def setup(flows: Flows, chat: FakeChat, store: InMemoryStore) -> tuple[str, int]:
    await flows.on_start(CHAT, None, is_forum=True)
    await flows.drain()
    for answer in ["Bright Coaching", "Career coaching", "Mid-career engineers"]:
        await say(flows, answer)
    card = chat.find("Setup done")
    await flows.on_callback(CHAT, card.message_id, card.data("Marketing"))
    await flows.drain()
    thread_id = chat.topics["Marketing"]
    for answer in ["Bluesky", "Friday launch", "list@example.com"]:
        await say(flows, answer, thread_id)
    business_id = (await store.list_businesses())[CHAT]
    return await store.dashboard_token(business_id), thread_id

async def overview(client, token: str) -> dict:
    response = await client.get(f"/api/b/{token}/overview")
    assert response.status_code == 200
    return response.json()

async def test_dashboard_command_sends_the_private_link(flows, chat, store):
    token, _ = await setup(flows, chat, store)
    await flows.on_dashboard(CHAT, None)
    assert f"https://t.example/b/{token}" in chat.last(None).text

async def test_unknown_link_is_404(client):
    assert (await client.get("/b/nope")).status_code == 404
    assert (await client.get("/api/b/nope/overview")).status_code == 404

async def test_page_is_served_privately(client, flows, chat, store):
    token, _ = await setup(flows, chat, store)
    response = await client.get(f"/b/{token}")
    assert response.status_code == 200
    assert "<title>Tenure</title>" in response.text
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"

async def test_overview_after_onboarding(client, flows, chat, store):
    token, _ = await setup(flows, chat, store)
    data = await overview(client, token)
    assert data["business"]["name"] == "Bright Coaching"
    assert data["lead"] == "Maya"
    (team,) = data["teams"]
    assert [p["name"] for p in team["people"]] == ["Maya", "Leo", "Sam"]
    assert [t["label"] for t in team["trust"]] == ["Bluesky post", "Newsletter", "Competitor check"]
    assert team["trust"][0]["level"] == 1
    assert {lesson["text"] for lesson in data["lessons"]} == {
        "Channels: Bluesky",
        "Upcoming: Friday launch",
        "Newsletter to: list@example.com",
    }
    assert [t["hired"] for t in data["templates"]] == [True, False]
    assert data["stats"]["waiting"] == 0

async def test_drafts_show_and_approve_reaches_telegram(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, store)
    await say(flows, REQUEST, thread_id)
    data = await overview(client, token)
    assert data["stats"]["waiting"] == 2
    post = next(d for d in data["drafts"] if d["type"] == "Bluesky post")
    assert post["check"] == 90 and post["team"] == "Marketing"

    response = await client.post(f"/api/b/{token}/approvals/{post['approval_id']}/approve")
    assert response.status_code == 200
    await flows.drain()
    card = chat.find("Draft for approval: <b>Bluesky post")
    assert card.text.endswith(f"<i>{ui.APPROVED_ON_DASHBOARD}</i>") and card.keyboard == []
    assert chat.find("Posted to Bluesky").thread_id == thread_id

    again = await client.post(f"/api/b/{token}/approvals/{post['approval_id']}/approve")
    assert again.status_code == 409

    data = await overview(client, token)
    assert data["stats"]["waiting"] == 1
    (action,) = data["activity"]
    assert action["summary"] == "Posted to Bluesky" and action["approved"]
    assert action["undo_until"] is not None
    assert data["stats"]["clean_rate"] == 100

async def test_reject_with_reason_revises_in_telegram(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, store)
    await say(flows, REQUEST, thread_id)
    post = next(d for d in (await overview(client, token))["drafts"] if d["type"] == "Bluesky post")
    response = await client.post(
        f"/api/b/{token}/approvals/{post['approval_id']}/reject", json={"reason": "Too salesy"}
    )
    assert "revise" in response.json()["message"]
    await flows.drain()
    assert "Too salesy" in chat.find("Learned:").text
    drafts = (await overview(client, token))["drafts"]
    assert len(drafts) == 2 and post["approval_id"] not in [d["approval_id"] for d in drafts]

async def test_reject_without_reason_drops_it(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, store)
    await say(flows, REQUEST, thread_id)
    post = next(d for d in (await overview(client, token))["drafts"] if d["type"] == "Bluesky post")
    await client.post(f"/api/b/{token}/approvals/{post['approval_id']}/reject", json={})
    await flows.drain()
    assert "Okay, dropped it." in chat.last(thread_id).text
    statuses = [t["status"] for t in (await overview(client, token))["tasks"]]
    assert "rejected" in statuses

async def test_lowering_trust_resets_the_streak(client, flows, chat, store):
    token, _ = await setup(flows, chat, store)
    team = (await overview(client, token))["teams"][0]
    body = {"team_id": team["team_id"], "task_type": "social_post"}
    assert (await client.post(f"/api/b/{token}/trust/lower", json=body)).status_code == 200
    trust = await store.get_trust(team["team_id"], "social_post")
    assert trust.level is AutonomyLevel.DRAFT_ONLY and trust.approval_streak == 0
    assert (await client.post(f"/api/b/{token}/trust/lower", json=body)).status_code == 409

async def test_forget_updates_store_and_telegram_card(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, store)
    await say(flows, "Stop using hashtags", thread_id)
    lesson = next(
        lesson for lesson in (await overview(client, token))["lessons"]
        if lesson["text"] == "Stop using hashtags"
    )
    response = await client.post(f"/api/b/{token}/lessons/{lesson['lesson_id']}/forget")
    assert response.status_code == 200
    texts = [lesson["text"] for lesson in (await overview(client, token))["lessons"]]
    assert "Stop using hashtags" not in texts
    card = chat.find("Learned:")
    assert "Forgotten" in card.text and card.keyboard == []

async def test_hire_from_the_dashboard_creates_the_topic(client, flows, chat, store):
    token, _ = await setup(flows, chat, store)
    response = await client.post(f"/api/b/{token}/hire", json={"template": "finance"})
    assert response.status_code == 200
    await flows.drain()
    assert "Finance" in chat.topics
    assert [t["hired"] for t in (await overview(client, token))["templates"]] == [True, True]
    unknown = await client.post(f"/api/b/{token}/hire", json={"template": "sales"})
    assert unknown.status_code == 409
