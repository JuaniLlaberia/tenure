import asyncio
import re
from datetime import timedelta

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
    return Flows(
        brain,
        chat,
        store=store,
        debounce=0,
        clock=clock,
        dashboard_url="https://t.example/",
        password_ttl=0.01,
    )

@pytest.fixture
async def client(flows, store, brain, clock):
    api = create_api(flows, store, brain, clock=clock)
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

async def say(flows: Flows, text: str, thread_id: int | None = None) -> None:
    await flows.on_text(CHAT, thread_id, text, 1, NOW)
    await flows.drain()

async def open_dashboard(flows: Flows, chat: FakeChat) -> tuple[str, str]:
    await flows.on_dashboard(CHAT, None)
    message = chat.last(None)
    text = message.text
    links = [button.url for row in message.keyboard for button in row if button.url]
    token = re.search(r"/b/([\w-]+)", links[0] if links else text).group(1)
    password = re.search(r"<code>([a-z0-9-]+)</code>", text).group(1)
    return token, password

async def login(client, token: str, password: str) -> httpx.Response:
    return await client.post(f"/b/{token}/api/login", json={"password": password})

async def onboard(flows: Flows, chat: FakeChat) -> int:
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
    return thread_id

async def setup(flows: Flows, chat: FakeChat, client) -> tuple[str, int]:
    thread_id = await onboard(flows, chat)
    token, password = await open_dashboard(flows, chat)
    assert (await login(client, token, password)).status_code == 200
    return token, thread_id

async def overview(client, token: str) -> dict:
    response = await client.get(f"/b/{token}/api/overview")
    assert response.status_code == 200
    return response.json()

async def test_dashboard_command_sends_link_and_password(flows, chat):
    await onboard(flows, chat)
    token, password = await open_dashboard(flows, chat)
    assert len(token) >= 20
    assert re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", password)

async def test_unknown_link_is_404(client):
    assert (await client.get("/b/nope")).status_code == 404
    assert (await client.get("/b/nope/api/overview")).status_code == 404

async def test_page_is_served_privately(client, flows, chat, store):
    token, _ = await setup(flows, chat, client)
    response = await client.get(f"/b/{token}")
    assert response.status_code == 200
    assert "<title>Tenure</title>" in response.text
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"

async def test_overview_after_onboarding(client, flows, chat, store):
    token, _ = await setup(flows, chat, client)
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
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    data = await overview(client, token)
    assert data["stats"]["waiting"] == 2
    post = next(d for d in data["drafts"] if d["type"] == "Bluesky post")
    assert post["check"] == 90 and post["team"] == "Marketing"

    response = await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    assert response.status_code == 200
    await flows.drain()
    card = chat.find("Draft for approval: <b>Bluesky post")
    assert card.text.endswith(f"<i>{ui.APPROVED_ON_DASHBOARD}</i>") and card.keyboard == []
    assert chat.find("Posted to Bluesky").thread_id == thread_id

    again = await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    assert again.status_code == 409

    data = await overview(client, token)
    assert data["stats"]["waiting"] == 1
    (action,) = data["activity"]
    assert action["summary"] == "Posted to Bluesky" and action["approved"]
    assert action["undo_until"] is not None
    assert data["stats"]["clean_rate"] == 100

async def test_reject_with_reason_revises_in_telegram(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    post = next(d for d in (await overview(client, token))["drafts"] if d["type"] == "Bluesky post")
    response = await client.post(
        f"/b/{token}/api/approvals/{post['approval_id']}/reject", json={"reason": "Too salesy"}
    )
    assert "revise" in response.json()["message"]
    await flows.drain()
    assert "Too salesy" in chat.find("Learned:").text
    drafts = (await overview(client, token))["drafts"]
    assert len(drafts) == 2 and post["approval_id"] not in [d["approval_id"] for d in drafts]

async def test_reject_without_reason_drops_it(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    post = next(d for d in (await overview(client, token))["drafts"] if d["type"] == "Bluesky post")
    await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/reject", json={})
    await flows.drain()
    assert "Okay, dropped it." in chat.last(thread_id).text
    statuses = [t["status"] for t in (await overview(client, token))["tasks"]]
    assert "rejected" in statuses

async def test_lowering_trust_resets_the_streak(client, flows, chat, store):
    token, _ = await setup(flows, chat, client)
    team = (await overview(client, token))["teams"][0]
    body = {"team_id": team["team_id"], "task_type": "social_post"}
    assert (await client.post(f"/b/{token}/api/trust/lower", json=body)).status_code == 200
    trust = await store.get_trust(team["team_id"], "social_post")
    assert trust.level is AutonomyLevel.DRAFT_ONLY and trust.approval_streak == 0
    assert (await client.post(f"/b/{token}/api/trust/lower", json=body)).status_code == 409

async def test_forget_updates_store_and_telegram_card(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, "Stop using hashtags", thread_id)
    lesson = next(
        lesson for lesson in (await overview(client, token))["lessons"]
        if lesson["text"] == "Stop using hashtags"
    )
    response = await client.post(f"/b/{token}/api/lessons/{lesson['lesson_id']}/forget")
    assert response.status_code == 200
    texts = [lesson["text"] for lesson in (await overview(client, token))["lessons"]]
    assert "Stop using hashtags" not in texts
    card = chat.find("Learned:")
    assert "Forgotten" in card.text and card.keyboard == []

async def test_hire_from_the_dashboard_creates_the_topic(client, flows, chat, store):
    token, _ = await setup(flows, chat, client)
    response = await client.post(f"/b/{token}/api/hire", json={"template": "finance"})
    assert response.status_code == 200
    await flows.drain()
    assert "Finance" in chat.topics
    assert [t["hired"] for t in (await overview(client, token))["templates"]] == [True, True]
    unknown = await client.post(f"/b/{token}/api/hire", json={"template": "sales"})
    assert unknown.status_code == 409

async def test_api_needs_the_password(client, flows, chat):
    await onboard(flows, chat)
    token, password = await open_dashboard(flows, chat)
    assert (await client.get(f"/b/{token}")).status_code == 200
    assert (await client.get(f"/b/{token}/api/overview")).status_code == 401
    hire = await client.post(f"/b/{token}/api/hire", json={"template": "finance"})
    assert hire.status_code == 401
    response = await login(client, token, f"  {password.upper()} ")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert f"Path=/b/{token}" in cookie
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    assert (await client.get(f"/b/{token}/api/overview")).status_code == 200
    await client.post(f"/b/{token}/api/logout")
    assert (await client.get(f"/b/{token}/api/overview")).status_code == 401

async def test_five_wrong_tries_lock_the_link(client, flows, chat, clock):
    await onboard(flows, chat)
    token, password = await open_dashboard(flows, chat)
    for left in (4, 3, 2, 1):
        response = await login(client, token, "wrong")
        assert response.status_code == 401 and f"{left} tr" in response.json()["detail"]
    assert (await login(client, token, "wrong")).status_code == 429
    assert (await login(client, token, password)).status_code == 429
    clock.now = NOW + timedelta(minutes=6)
    assert (await login(client, token, password)).status_code == 200

async def test_new_password_signs_out_old_browsers(client, flows, chat):
    token, _ = await setup(flows, chat, client)
    again, password = await open_dashboard(flows, chat)
    assert again == token
    assert (await client.get(f"/b/{token}/api/overview")).status_code == 401
    assert (await login(client, token, password)).status_code == 200

async def test_dashboard_message_has_open_and_copy_buttons(flows, chat):
    await onboard(flows, chat)
    token, password = await open_dashboard(flows, chat)
    (copy, open_link), = chat.last(None).keyboard
    assert copy.copy == password
    assert open_link.url == f"https://t.example/b/{token}"
    assert "https://" not in chat.last(None).text

async def test_refused_link_button_falls_back_to_a_text_link(flows, chat):
    await onboard(flows, chat)
    send = chat.send

    async def no_url_buttons(chat_id, thread_id, text, keyboard=None):
        if any(button.url for row in keyboard or [] for button in row):
            raise RuntimeError("Bad Request: wrong HTTP URL")
        return await send(chat_id, thread_id, text, keyboard)

    chat.send = no_url_buttons
    token, password = await open_dashboard(flows, chat)
    message = chat.last(None)
    assert f"https://t.example/b/{token}" in message.text
    assert [[button.copy for button in row] for row in message.keyboard] == [[password]]

async def test_password_message_deletes_itself(flows, chat):
    await onboard(flows, chat)
    await flows.on_dashboard(CHAT, None)
    message = chat.last(None)
    await asyncio.sleep(0.05)
    assert message.message_id in chat.deleted

async def test_dashboard_stop_turns_the_link_off(client, flows, chat):
    token, _ = await setup(flows, chat, client)
    await flows.on_dashboard_stop(CHAT, None)
    assert chat.last(None).text == ui.DASHBOARD_STOPPED
    assert (await client.get(f"/b/{token}")).status_code == 404
    assert (await client.get(f"/b/{token}/api/overview")).status_code == 404
    new_token, password = await open_dashboard(flows, chat)
    assert new_token != token
    assert (await login(client, new_token, password)).status_code == 200

async def test_threshold_sets_every_task_type_of_the_team(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    team = (await overview(client, token))["teams"][0]
    assert team["promote_after"] == 5
    body = {"team_id": team["team_id"], "promote_after": 1}
    response = await client.post(f"/b/{token}/api/trust/threshold", json=body)
    assert response.status_code == 200
    trust = (await overview(client, token))["teams"][0]["trust"]
    assert {t["promote_after"] for t in trust} == {1}

    await say(flows, REQUEST, thread_id)
    post = next(d for d in (await overview(client, token))["drafts"] if d["type"] == "Bluesky post")
    await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    await flows.drain()
    assert chat.find("Can I take the next step?")

async def test_threshold_stays_within_bounds(client, flows, chat):
    token, _ = await setup(flows, chat, client)
    team_id = (await overview(client, token))["teams"][0]["team_id"]
    for value in (0, 21):
        body = {"team_id": team_id, "promote_after": value}
        assert (await client.post(f"/b/{token}/api/trust/threshold", json=body)).status_code == 422

async def test_dashboard_refuses_a_second_team_of_the_same_kind(client, flows, chat, store):
    token, _ = await setup(flows, chat, client)
    response = await client.post(f"/b/{token}/api/hire", json={"template": "marketing"})
    assert response.status_code == 409
    assert "already have a Marketing team" in response.json()["message"]

async def post_draft(client, token: str, kind: str = "Bluesky post") -> dict:
    return next(d for d in (await overview(client, token))["drafts"] if d["type"] == kind)

async def test_drafts_carry_review_context(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, "Stop using hashtags", thread_id)
    await say(flows, REQUEST, thread_id)
    post = await post_draft(client, token)
    assert post["kind"] == "post" and post["text"].startswith("Big news:")
    assert post["asked"] == REQUEST
    assert post["steps"] == ["Leo (writer)"]
    assert post["lead"] == "Maya" and post["streak"] == 0 and post["promote_after"] == 5
    assert "Stop using hashtags" in post["rules"]
    email = await post_draft(client, token, "Newsletter")
    assert email["kind"] == "email" and email["to"] == "list@example.com"
    assert email["steps"] == ["Sam (researcher)", "Leo (writer)"]

    await client.post(
        f"/b/{token}/api/approvals/{post['approval_id']}/reject", json={"reason": "Too salesy"}
    )
    await flows.drain()
    revised = await post_draft(client, token)
    assert revised["revision"] == 1 and revised["revised_after"] == "Too salesy"

async def test_editing_a_post_from_the_dashboard(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    post = await post_draft(client, token)
    url = f"/b/{token}/api/approvals/{post['approval_id']}/approve"
    too_long = await client.post(url, json={"text": "x" * 301})
    assert too_long.status_code == 409 and "301 characters" in too_long.json()["message"]

    response = await client.post(url, json={"text": "Launching Friday. Come along."})
    assert "learn" in response.json()["message"]
    await flows.drain()
    card = chat.find("Draft for approval: <b>Bluesky post")
    assert card.text.endswith(f"<i>{ui.EDITED_ON_DASHBOARD}</i>")
    approval = await store.get_approval(post["approval_id"])
    assert approval.status == "edited" and approval.edited_text == "Launching Friday. Come along."
    assert chat.find("Learned:")

async def test_unchanged_text_is_a_plain_approval(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    post = await post_draft(client, token)
    url = f"/b/{token}/api/approvals/{post['approval_id']}/approve"
    await client.post(url, json={"text": post["text"]})
    await flows.drain()
    assert (await store.get_approval(post["approval_id"])).status == "approved"

async def test_editing_an_email_subject_and_recipient(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    email = await post_draft(client, token, "Newsletter")
    url = f"/b/{token}/api/approvals/{email['approval_id']}/approve"
    bad = await client.post(url, json={"to": "not an address"})
    assert bad.status_code == 409

    await client.post(url, json={"to": "team@bright.example", "subject": "Friday!"})
    await flows.drain()
    assert chat.find("Sent the email to team@bright.example")
    approval = await store.get_approval(email["approval_id"])
    assert approval.status == "edited"

async def test_dashboard_decision_cancels_a_telegram_edit_prompt(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    card = chat.find("Draft for approval: <b>Bluesky post")
    await flows.on_callback(CHAT, card.message_id, card.data("Edit"))
    post = await post_draft(client, token)
    await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    await flows.drain()
    assert (CHAT, thread_id) not in flows._state.pending
    await say(flows, "We launch our new package on Friday morning, tell people", thread_id)
    assert "waiting for your OK" in chat.last(thread_id).text

async def test_undo_from_the_dashboard(client, flows, chat, store, clock):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    post = await post_draft(client, token)
    await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    await flows.drain()
    (action,) = (await overview(client, token))["activity"]
    assert action["undo_until"] is not None

    response = await client.post(f"/b/{token}/api/actions/{action['action_id']}/undo")
    assert response.status_code == 200
    await flows.drain()
    assert chat.find("Deleted the Bluesky post").text.startswith("<s>Posted to Bluesky</s>")
    again = await client.post(f"/b/{token}/api/actions/{action['action_id']}/undo")
    assert again.status_code == 409

async def test_undo_after_the_window_is_refused(client, flows, chat, store, clock):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, REQUEST, thread_id)
    post = await post_draft(client, token)
    await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    await flows.drain()
    action_id = (await store.list_actions((await store.list_businesses())[CHAT]))[0].action_id
    clock.now = NOW + timedelta(minutes=11)
    response = await client.post(f"/b/{token}/api/actions/{action_id}/undo")
    assert response.status_code == 409 and "window" in response.json()["message"]
