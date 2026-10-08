import json
from types import SimpleNamespace

import httpx
import pytest
from atproto_client.exceptions import InvokeTimeoutError, NetworkError, UnauthorizedError
from tests.app.fakes import NOW, Clock

from app.fake_brain import FakeBrain
from app.store.memory import InMemoryStore
from app.tools.bluesky import Bluesky, rich_text
from app.tools.email import Resend, ResendError, markdown_to_html
from app.tools.real import NO_BLUESKY, NO_EMAIL, RealTools
from app.tools.web import TEXT_LIMIT, Web, is_public, parse_results
from contract import (
    ActionDone,
    ActionResult,
    ActionUndone,
    ApprovalDecision,
    Error,
    IncomingMessage,
    NeedsApproval,
)

URI = "at://did:plc:abc/app.bsky.feed.post/3kxyz"

class FakeAtproto:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = failures or []
        self.logins = 0
        self.posts: list[str] = []
        self.deleted: list[str] = []

    async def login(self, handle, password):
        self.logins += 1

    async def send_post(self, builder):
        if self.failures:
            raise self.failures.pop(0)
        self.posts.append(builder.build_text())
        return SimpleNamespace(uri=URI)

    async def delete_post(self, uri):
        self.deleted.append(uri)
        return True

def test_rich_text_makes_links_and_hashtags_clickable():
    text = "Launch Friday! See https://bright.example/launch. #coaching rocks"
    builder = rich_text(text)
    assert builder.build_text() == text
    kinds = [f.features[0].py_type for f in builder.build_facets()]
    assert kinds == ["app.bsky.richtext.facet#link", "app.bsky.richtext.facet#tag"]

async def test_bluesky_posts_and_builds_the_public_url():
    client = FakeAtproto()
    bluesky = Bluesky("@demo.bsky.social", "app-pass", make_client=lambda: client)
    uri, url = await bluesky.post("Hello")
    assert uri == URI
    assert url == "https://bsky.app/profile/demo.bsky.social/post/3kxyz"
    await bluesky.delete(uri)
    assert client.deleted == [URI] and client.logins == 1

async def test_bluesky_signs_in_again_when_the_session_expired():
    client = FakeAtproto([UnauthorizedError()])
    bluesky = Bluesky("demo.bsky.social", "app-pass", make_client=lambda: client)
    await bluesky.post("Hello")
    assert client.logins == 2 and client.posts == ["Hello"]

async def test_bluesky_retries_a_timed_out_sign_in():
    class SlowLogin(FakeAtproto):
        async def login(self, handle, password):
            self.logins += 1
            if self.logins == 1:
                raise InvokeTimeoutError()

    client = SlowLogin()
    bluesky = Bluesky("demo.bsky.social", "app-pass", make_client=lambda: client)
    await bluesky.post("Hello")
    assert client.logins == 2 and client.posts == ["Hello"]

async def test_bluesky_does_not_retry_other_failures():
    client = FakeAtproto([NetworkError()])
    bluesky = Bluesky("demo.bsky.social", "app-pass", make_client=lambda: client)
    with pytest.raises(NetworkError):
        await bluesky.post("Hello")
    assert client.posts == []

def test_markdown_to_html():
    html = markdown_to_html(
        "Hi **there**,\n\n- one\n- [two](https://x.example)\n\nSee https://y.example <now>"
    )
    assert "<strong>there</strong>" in html
    assert '<ul><li>one</li><li><a href="https://x.example">two</a></li></ul>' in html
    assert '<a href="https://y.example">https://y.example</a> &lt;now&gt;' in html

def mock_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

async def test_resend_sends_text_and_html():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "email-1"})

    resend = Resend("re_key", "Tenure <onboarding@resend.dev>", http=mock_http(handler))
    assert await resend.send(" Mark@B.co ", "Launch", "Hi **all**") == "email-1"
    assert seen["auth"] == "Bearer re_key"
    assert seen["body"]["to"] == ["mark@b.co"] and seen["body"]["text"] == "Hi **all**"
    assert "<strong>all</strong>" in seen["body"]["html"]

async def test_resend_errors_carry_the_reason():
    def handler(request):
        return httpx.Response(403, json={"message": "You can only send testing emails to you"})

    resend = Resend("re_key", "x@y.z", http=mock_http(handler))
    with pytest.raises(ResendError, match="testing emails"):
        await resend.send("a@b.co", "S", "B")

SEARCH_PAGE = """
<div class="result results_links result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
<div class="result"><h2><a class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcoach.example%2Fpricing&rut=x"
  >Coach <b>Pricing</b></a></h2>
<a class="result__snippet" href="#">Packages from <b>$500</b> a month.</a></div>
<div class="result"><h2><a class="result__a" href="https://direct.example/">Direct</a></h2>
<a class="result__snippet">Second.</a></div>
"""

def test_search_results_are_parsed_and_unwrapped():
    results = parse_results(SEARCH_PAGE, k=5)
    assert [r.url for r in results] == ["https://coach.example/pricing", "https://direct.example/"]
    assert results[0].title == "Coach Pricing"
    assert results[0].snippet == "Packages from $500 a month."
    assert len(parse_results(SEARCH_PAGE, k=1)) == 1

async def test_private_addresses_are_refused():
    for url in ("http://localhost:8000/b/x", "http://127.0.0.1/", "http://10.0.0.5/", "ftp://x.example"):
        assert not await is_public(url)

PAGE = """<html><head><title> Bright  Coaching </title><style>p{}</style></head>
<body><nav>Menu Home About</nav><h1>Coaching for engineers</h1>
<p>We help   mid-career engineers.</p><script>track()</script>
<footer>© 2026</footer></body></html>"""

async def always_public(url: str) -> bool:
    return True

async def test_fetch_returns_clean_text():
    def handler(request):
        return httpx.Response(200, html=PAGE)

    page = await Web(http=mock_http(handler), check_address=always_public).fetch("https://b.example")
    assert page.title == "Bright Coaching"
    assert page.text == "Coaching for engineers\n\nWe help mid-career engineers."

async def test_fetch_checks_every_redirect():
    def handler(request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        return httpx.Response(200, html=PAGE)

    async def only_public_host(url: str) -> bool:
        return "public.example" in url

    web = Web(http=mock_http(handler), check_address=only_public_host)
    assert await web.fetch("https://public.example/") is None

async def test_fetch_skips_non_text_and_truncates():
    def handler(request):
        if request.url.path == "/file.pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(200, text="x" * (TEXT_LIMIT + 50))

    web = Web(http=mock_http(handler), check_address=always_public)
    assert await web.fetch("https://b.example/file.pdf") is None
    assert len((await web.fetch("https://b.example/long.txt")).text) == TEXT_LIMIT

async def test_real_tools_report_missing_connections():
    tools = RealTools()
    post = await tools.post_social("b1", "Hi")
    email = await tools.send_email("b1", "a@b.co", "S", "B")
    assert (post.ok, post.error) == (False, NO_BLUESKY)
    assert (email.ok, email.error) == (False, NO_EMAIL)

async def test_real_tools_never_raise():
    class Broken:
        async def post(self, text):
            raise RuntimeError("bluesky   is down")

        async def search(self, query, k):
            raise RuntimeError("blocked")

    tools = RealTools(bluesky=Broken(), web=Broken())
    result = await tools.post_social("b1", "Hi")
    assert not result.ok and result.error == "bluesky is down" and result.action_id
    assert await tools.web_search("coaching") == []

class RecordingTools:
    def __init__(self, fail_posts: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail_posts = fail_posts

    async def post_social(self, business_id, text):
        self.calls.append(("post", text))
        if self.fail_posts:
            return ActionResult(action_id="x1", ok=False, error="Bluesky is down")
        return ActionResult(action_id="x1", ok=True, url="https://bsky.app/p/1", external_id=URI)

    async def delete_social(self, business_id, external_id):
        self.calls.append(("delete", external_id))
        return ActionResult(action_id="x2", ok=True)

    async def send_email(self, business_id, to, subject, body):
        self.calls.append(("email", to))
        return ActionResult(action_id="x3", ok=True, external_id="email-1")

    async def web_search(self, query, k=5):
        return []

    async def fetch_page(self, url):
        return None

async def run(events):
    return [event async for event in events]

def msg(text, team_id=None):
    return IncomingMessage(
        business_id="b1", team_id=team_id, text=text, message_id="1", sent_at=NOW
    )

async def drafted(brain: FakeBrain) -> tuple[str, list[NeedsApproval]]:
    await run(brain.start_onboarding("b1"))
    for answer in ["Bright Coaching", "Coaching", "Engineers"]:
        await run(brain.handle_message(msg(answer)))
    team_id = (await run(brain.hire_team("b1", "marketing")))[0].team_id
    for answer in ["Bluesky", "Friday", "Send it to Me@Bright.example please"]:
        await run(brain.handle_message(msg(answer, team_id)))
    events = await run(brain.handle_message(msg("We launch on Friday, tell everyone", team_id)))
    return team_id, [e for e in events if isinstance(e, NeedsApproval)]

def decide(approval_id, decision="approve"):
    return ApprovalDecision(business_id="b1", approval_id=approval_id, decision=decision)

async def test_fake_brain_posts_and_undoes_through_the_tools():
    tools = RecordingTools()
    brain = FakeBrain(InMemoryStore(), tools=tools, clock=Clock())
    _, (post, email) = await drafted(brain)
    assert email.planned_action.to == "me@bright.example"
    events = await run(brain.resolve_approval(decide(post.approval_id)))
    (done,) = [e for e in events if isinstance(e, ActionDone)]
    assert done.url == "https://bsky.app/p/1" and done.action_id == "x1"
    undone = await run(brain.undo_action("b1", done.action_id))
    assert isinstance(undone[0], ActionUndone)
    assert tools.calls == [("post", post.planned_action.text), ("delete", URI)]

async def test_failed_post_reports_and_does_not_count():
    store = InMemoryStore()
    brain = FakeBrain(store, tools=RecordingTools(fail_posts=True), clock=Clock())
    team_id, (post, _) = await drafted(brain)
    events = await run(brain.resolve_approval(decide(post.approval_id)))
    assert [type(e) for e in events] == [Error]
    assert "Bluesky is down" in events[0].message
    assert (await store.get_task(post.task_id)).status == "failed"
    assert (await store.get_trust(team_id, "social_post")).approval_streak == 0
    assert (await store.get_action("x1")).result.ok is False
