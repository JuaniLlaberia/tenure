from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image as PILImage
from tests.app.fakes import NOW, Clock, FakeBluesky, FakeChat
from tests.app.test_dashboard import CHAT, open_dashboard, overview, say, setup

from app.chat.flows import Flows
from app.chat.port import IncomingFile
from app.fake_brain import FakeBrain
from app.files import Files
from app.store.memory import InMemoryStore
from app.tools.real import RealTools
from app.web import api as web_api
from app.web.api import create_api
from contract import ApprovalDecision, PostSocial

WEEKLY = "Every Monday, research a topic and propose a newsletter"

def png(color=(47, 93, 80), size=(600, 400)) -> bytes:
    out = BytesIO()
    PILImage.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()

class Recording(FakeBrain):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.decisions: list[ApprovalDecision] = []

    def resolve_approval(self, decision):
        self.decisions.append(decision)
        return super().resolve_approval(decision)

@pytest.fixture
def clock() -> Clock:
    return Clock()

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def chat() -> FakeChat:
    return FakeChat()

def tools_for(store, clock) -> RealTools:
    return RealTools(bluesky=FakeBluesky(), files=Files(store, clock))

@pytest.fixture
def brain(store, clock) -> Recording:
    return Recording(store, tools=tools_for(store, clock), clock=clock)

@pytest.fixture
def flows(brain, chat, store, clock) -> Flows:
    return Flows(
        brain, chat, store=store, debounce=0, clock=clock, dashboard_url="https://t.example/"
    )

@pytest.fixture
async def client(flows, store, brain, clock):
    app = create_api(flows, store, brain, clock=clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

async def photo_post(flows: Flows, chat: FakeChat, thread_id: int) -> None:
    chat.files.update({"p1": png(), "p2": png((168, 102, 11))})
    await flows.on_file(CHAT, thread_id, "Post these", IncomingFile("p1", "image/jpeg"), 2, NOW)
    await flows.on_file(CHAT, thread_id, "", IncomingFile("p2", "image/jpeg"), 3, NOW)
    await flows.drain()

async def drafts_of(client, token: str, kind: str) -> list[dict]:
    return [d for d in (await overview(client, token))["drafts"] if d["kind"] == kind]

async def test_draft_images_and_files_route(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await photo_post(flows, chat, thread_id)
    (post,) = await drafts_of(client, token, "post")
    assert len(post["images"]) == 2
    file_id = post["images"][0]["file_id"]

    full = await client.get(f"/b/{token}/api/files/{file_id}")
    assert full.status_code == 200 and full.content == png()
    assert full.headers["content-type"] == "image/jpeg"
    assert full.headers["cache-control"] == "private, max-age=3600"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert full.headers["content-security-policy"] == "sandbox"
    assert "content-disposition" not in full.headers
    thumb = await client.get(f"/b/{token}/api/files/{file_id}?thumb=1")
    assert thumb.headers["content-type"] == "image/jpeg" and thumb.content[:2] == b"\xff\xd8"

    other = await Files(store).save("someone-else", png(), "image/png")
    assert (await client.get(f"/b/{token}/api/files/{other.file_id}")).status_code == 404
    assert (await client.get(f"/b/{token}/api/files/missing")).status_code == 404
    client.cookies.clear()
    assert (await client.get(f"/b/{token}/api/files/{file_id}")).status_code == 401

async def test_removing_an_image_is_an_edit_with_the_kept_ones(client, flows, chat, brain):
    token, thread_id = await setup(flows, chat, client)
    await photo_post(flows, chat, thread_id)
    (post,) = await drafts_of(client, token, "post")
    first, second = (image["file_id"] for image in post["images"])
    url = f"/b/{token}/api/approvals/{post['approval_id']}/approve"

    response = await client.post(url, json={"images": [second]})
    assert response.status_code == 200
    await flows.drain()
    (decision,) = brain.decisions
    assert decision.decision == "edit"
    assert isinstance(decision.edited_action, PostSocial)
    assert [ref.file_id for ref in decision.edited_action.images] == [second]
    assert decision.edited_text == post["text"]

async def test_unknown_or_repeated_images_are_refused(client, flows, chat, brain):
    token, thread_id = await setup(flows, chat, client)
    await photo_post(flows, chat, thread_id)
    (post,) = await drafts_of(client, token, "post")
    first = post["images"][0]["file_id"]
    url = f"/b/{token}/api/approvals/{post['approval_id']}/approve"
    for keep in (["not-in-draft"], [first, first]):
        response = await client.post(url, json={"images": keep})
        assert response.status_code == 409
    assert brain.decisions == []

async def test_keeping_every_image_is_a_plain_approval(client, flows, chat, brain):
    token, thread_id = await setup(flows, chat, client)
    await photo_post(flows, chat, thread_id)
    (post,) = await drafts_of(client, token, "post")
    keep = [image["file_id"] for image in post["images"]]
    url = f"/b/{token}/api/approvals/{post['approval_id']}/approve"
    await client.post(url, json={"images": keep, "text": post["text"]})
    await flows.drain()
    assert [d.decision for d in brain.decisions] == ["approve"]

async def test_design_drafts_show_their_media(client, flows, chat):
    token, _ = await setup(flows, chat, client)
    hired = await client.post(f"/b/{token}/api/hire", json={"template": "design"})
    assert hired.status_code == 200
    await flows.drain()
    thread_id = chat.topics["Design"]
    for answer in ["Green and amber", "Illustration", "Nothing in particular"]:
        await say(flows, answer, thread_id)
    await say(flows, "Make an image for our Friday launch", thread_id)
    (visual,) = await drafts_of(client, token, "draft")
    assert visual["type"] == "Visual" and len(visual["images"]) == 1
    assert visual["lead_avatar"] == "/avatars/design/iris.png"

async def test_overview_has_avatars_and_activity_images(client, flows, chat):
    token, thread_id = await setup(flows, chat, client)
    data = await overview(client, token)
    maya = data["teams"][0]["people"][0]
    assert maya == {
        "name": "Maya", "role": "Marketing lead", "avatar": "/avatars/marketing/maya.png"
    }
    assert data["templates"][0]["personas"][0]["avatar"] == "/avatars/marketing/maya.png"
    assert any(lesson["lead"]["name"] == "Maya" for lesson in data["lessons"] if lesson["lead"])

    await photo_post(flows, chat, thread_id)
    (post,) = await drafts_of(client, token, "post")
    await client.post(f"/b/{token}/api/approvals/{post['approval_id']}/approve")
    await flows.drain()
    (action, *_) = (await overview(client, token))["activity"]
    assert len(action["images"]) == 2

async def test_avatars_are_served_without_traversal(client, tmp_path, monkeypatch):
    (tmp_path / "marketing").mkdir()
    (tmp_path / "marketing" / "maya.png").write_bytes(png())
    (tmp_path.parent / "secret.png").write_bytes(b"no")
    monkeypatch.setattr(web_api, "AVATARS", tmp_path)
    found = await client.get("/avatars/marketing/maya.png")
    assert found.status_code == 200 and found.headers["content-type"] == "image/png"
    assert "max-age=86400" in found.headers["cache-control"]
    for path in ("../secret.png", "..%2Fsecret.png", "marketing/missing.png", "marketing"):
        assert (await client.get(f"/avatars/{path}")).status_code == 404

async def test_schedules_run_stop_and_start_from_the_dashboard(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, WEEKLY, thread_id)
    (schedule,) = (await overview(client, token))["schedules"]
    assert schedule["active"] and schedule["team"] == "Marketing"
    assert schedule["cadence"].startswith("Every Monday at 9:00")
    assert schedule["next_run_at"] is not None and schedule["lead"]["name"] == "Maya"
    base = f"/b/{token}/api/schedules/{schedule['schedule_id']}"

    assert (await client.post(f"{base}/run")).status_code == 200
    await flows.drain()
    assert len(await drafts_of(client, token, "post")) == 1
    ran = (await overview(client, token))["schedules"][0]
    assert ran["last_run_at"] is not None and not ran["running"]

    assert (await client.post(f"{base}/stop")).status_code == 200
    stopped = (await overview(client, token))["schedules"][0]
    assert not stopped["active"] and stopped["next_run_at"] is None
    refused = await client.post(f"{base}/run")
    assert refused.status_code == 409 and "Turn it back on" in refused.json()["message"]

    assert (await client.post(f"{base}/start")).status_code == 200
    assert (await overview(client, token))["schedules"][0]["active"]
    unknown = f"/b/{token}/api/schedules/{uuid4()}/run"
    assert (await client.post(unknown)).status_code == 409
    bad = await client.post(f"/b/{token}/api/schedules/nope/run")
    assert bad.status_code == 404 and bad.json()["detail"] == web_api.MISSING["schedule"]

async def test_files_that_could_run_as_pages_download_instead(client, flows, chat, store):
    token, _ = await setup(flows, chat, client)
    business_id = flows._state.businesses[CHAT]
    svg = await Files(store).save(business_id, b"<svg onload='alert(1)'/>", "image/svg+xml")
    page = await Files(store).save(business_id, b"<script>alert(1)</script>", "text/html")

    for ref in (svg, page):
        response = await client.get(f"/b/{token}/api/files/{ref.file_id}")
        assert response.status_code == 200
        assert response.headers["content-disposition"] == "attachment"
        assert response.headers["content-security-policy"] == "sandbox"

async def test_malformed_ids_and_bodies_get_readable_errors(client, flows, chat):
    token, _ = await setup(flows, chat, client)
    routes = {
        f"/b/{token}/api/approvals/x'1/approve": "draft",
        f"/b/{token}/api/approvals/nope/reject": "draft",
        f"/b/{token}/api/approvals/nope/new-image": "draft",
        f"/b/{token}/api/actions/nope/undo": "action",
        f"/b/{token}/api/lessons/nope/forget": "lesson",
        f"/b/{token}/api/schedules/nope/stop": "schedule",
    }
    for url, what in routes.items():
        response = await client.post(url, json={})
        assert response.status_code == 404 and response.json()["detail"] == web_api.MISSING[what]
    lower = await client.post(
        f"/b/{token}/api/trust/lower", json={"team_id": "nope", "task_type": "social_post"}
    )
    assert lower.status_code == 404 and lower.json()["detail"] == web_api.MISSING["team"]

    broken = await client.post(f"/b/{token}/api/trust/lower", content=b"{not json")
    assert broken.status_code == 422 and broken.json() == {"message": web_api.BAD_REQUEST}

async def test_another_business_cannot_touch_a_schedule(client, flows, chat, store):
    token, thread_id = await setup(flows, chat, client)
    await say(flows, WEEKLY, thread_id)
    (schedule,) = await store.list_schedules((await store.list_businesses())[CHAT])
    await store.save_schedule(schedule.model_copy(update={"business_id": "other"}))
    response = await client.post(f"/b/{token}/api/schedules/{schedule.schedule_id}/stop")
    assert response.status_code == 409

async def test_page_has_the_tabs(client, flows, chat):
    await setup(flows, chat, client)
    token, _ = await open_dashboard(flows, chat)
    page = (await client.get(f"/b/{token}")).text
    for tab in ("home", "drafts", "teams", "schedules", "knowledge", "activity", "spend"):
        assert f'id="t-{tab}"' in page

async def image_post(flows: Flows, thread_id: int) -> None:
    await say(flows, "We launch our new coaching package on Friday, with an image", thread_id)

async def test_new_image_from_the_dashboard_keeps_the_text(client, flows, chat, store, brain):
    token, thread_id = await setup(flows, chat, client)
    await image_post(flows, thread_id)
    (post,) = await drafts_of(client, token, "post")
    assert post["new_image"] and post["images"][0]["made"]

    url = f"/b/{token}/api/approvals/{post['approval_id']}/new-image"
    response = await client.post(url, json={"reason": "Warmer colours"})
    assert response.status_code == 200
    await flows.drain()

    (decision,) = brain.decisions
    assert (decision.decision, decision.reason) == ("new_image", "Warmer colours")
    (again,) = await drafts_of(client, token, "post")
    assert again["text"] == post["text"]
    assert again["images"][0]["file_id"] != post["images"][0]["file_id"]

async def test_new_image_is_refused_without_revisions_left(client, flows, chat, store, brain):
    token, thread_id = await setup(flows, chat, client)
    await image_post(flows, thread_id)
    (post,) = await drafts_of(client, token, "post")
    approval = await store.get_approval(post["approval_id"])
    task = await store.get_task(approval.task_id)
    await store.save_task(task.model_copy(update={"revisions": 2}))

    url = f"/b/{token}/api/approvals/{post['approval_id']}/new-image"
    response = await client.post(url, json={})
    assert response.status_code == 409
    assert "out of revisions" in response.json()["message"]
    assert brain.decisions == []

async def test_founder_photos_offer_no_new_image(client, flows, chat):
    token, thread_id = await setup(flows, chat, client)
    await photo_post(flows, chat, thread_id)
    (post,) = await drafts_of(client, token, "post")
    assert not post["new_image"] and not any(i["made"] for i in post["images"])
