import base64
import json
import random
from io import BytesIO

import httpx
import pytest
from PIL import Image as PILImage
from tests.app.fakes import NOW, Clock

from app.files import Files, kind_of
from app.store.memory import InMemoryStore
from app.tools import images
from app.tools.bluesky import Image
from app.tools.email import Resend
from app.tools.real import RealTools
from contract import Cadence, FileKind, Schedule

def png(width: int = 40, height: int = 30) -> bytes:
    out = BytesIO()
    PILImage.new("RGB", (width, height), (47, 93, 80)).save(out, "PNG")
    return out.getvalue()

def noise(side: int) -> bytes:
    out = BytesIO()
    pixels = random.Random(1).randbytes(side * side * 3)
    PILImage.frombytes("RGB", (side, side), pixels).save(out, "PNG")
    return out.getvalue()

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()

@pytest.fixture
def files(store) -> Files:
    return Files(store, Clock())

@pytest.mark.parametrize(
    "mime, kind",
    [
        ("image/png", FileKind.IMAGE),
        ("audio/ogg", FileKind.AUDIO),
        ("video/mp4", FileKind.VIDEO),
        ("application/pdf", FileKind.DOCUMENT),
        ("text/plain", FileKind.DOCUMENT),
    ],
)
def test_kind_follows_the_mime_type(mime, kind):
    assert kind_of(mime) == kind

async def test_saved_files_read_back_only_for_their_business(files):
    ref = await files.save("b1", b"voice", "audio/ogg", name="note.ogg", source="founder")
    assert (ref.kind, ref.size_bytes, ref.source, ref.created_at) == (
        FileKind.AUDIO,
        5,
        "founder",
        NOW,
    )
    assert await files.read("b1", ref.file_id) == b"voice"
    assert await files.read("b2", ref.file_id) is None
    assert await files.read("b1", "missing") is None

def test_small_images_are_left_alone():
    data = png()
    assert images.fit(data, "image/png") == (data, "image/png")

def test_big_images_are_shrunk_under_the_limit():
    data = noise(900)
    assert len(data) > 300_000
    out, mime = images.fit(data, "image/png", limit=300_000)
    assert mime == "image/jpeg" and len(out) <= 300_000

def test_unsupported_types_become_jpeg():
    out = BytesIO()
    PILImage.new("RGB", (20, 20)).save(out, "BMP")
    data, mime = images.fit(out.getvalue(), "image/bmp")
    assert mime == "image/jpeg" and images.size_of(data) == (20, 20)

def transparent_png(side: int = 40) -> bytes:
    out = BytesIO()
    PILImage.new("RGBA", (side, side), (0, 0, 0, 0)).save(out, "PNG")
    return out.getvalue()

def pixel(data: bytes) -> tuple:
    with PILImage.open(BytesIO(data)) as image:
        return image.convert("RGB").getpixel((5, 5))

def test_transparent_parts_turn_white_not_black():
    assert min(pixel(images.thumbnail(transparent_png()))) > 240
    out, mime = images.fit(transparent_png(), "image/x-other")
    assert mime == "image/jpeg" and min(pixel(out)) > 240

def test_an_animated_gif_becomes_its_first_frame():
    frames = [PILImage.new("P", (30, 30), color) for color in (1, 2, 3)]
    out = BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], transparency=0)
    data, mime = images.fit(out.getvalue(), "image/gif")
    assert mime == "image/jpeg" and images.size_of(data) == (30, 30)

def test_email_images_are_shrunk_under_a_megabyte():
    data, mime = images.for_email(noise(1600), "image/png")
    assert mime == "image/jpeg" and len(data) <= images.EMAIL_LIMIT
    assert max(images.size_of(data)) <= images.EMAIL_SIDE

def test_thumbnail_fits_the_side():
    thumb = images.thumbnail(png(1200, 600), side=300)
    assert images.size_of(thumb) == (300, 150)

class RecordingBluesky:
    def __init__(self) -> None:
        self.posts: list[tuple[str, list[Image]]] = []

    async def post(self, text, images=None):
        self.posts.append((text, images or []))
        return "at://post/1", "https://bsky.app/profile/x/post/1"

async def test_tools_store_and_read_generated_files(files):
    tools = RealTools(files=files)
    ref = await tools.save_file("b1", png(), "image/png", alt_text="A green square")
    assert ref.source == "generated" and ref.alt_text == "A green square"
    assert await tools.read_file("b1", ref.file_id) == png()
    assert await RealTools().save_file("b1", b"x", "image/png") is None

async def test_post_with_images_sends_bytes_alt_text_and_size(files):
    bluesky = RecordingBluesky()
    tools = RealTools(bluesky=bluesky, files=files)
    ref = await tools.save_file("b1", png(40, 30), "image/png", alt_text="A green square")

    result = await tools.post_social("b1", "Look", [ref])

    assert result.ok
    (text, sent), = bluesky.posts
    assert text == "Look"
    assert [(i.alt, i.width, i.height) for i in sent] == [("A green square", 40, 30)]

async def test_post_with_a_missing_image_fails_softly(files):
    tools = RealTools(bluesky=RecordingBluesky(), files=files)
    ref = await tools.save_file("b1", png(), "image/png")
    other = ref.model_copy(update={"file_id": "gone"})
    result = await tools.post_social("b1", "Look", [other])
    assert not result.ok and "Couldn't find the image" in result.error

async def test_email_images_go_inline_header_first(files):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "em_1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    email = Resend("re_x", "Bright <hi@b.co>", http=http)
    tools = RealTools(email=email, files=files)
    header = await tools.save_file("b1", png(), "image/png", alt_text="Studio")
    second = await tools.save_file("b1", png(), "image/png", alt_text="Chair")

    result = await tools.send_email("b1", "list@b.co", "Friday", "Doors open.", [header, second])

    assert result.ok
    body = seen["body"]
    assert [a["content_id"] for a in body["attachments"]] == ["img1", "img2"]
    assert base64.b64decode(body["attachments"][0]["content"]) == png()
    html = body["html"]
    assert html.index("cid:img1") < html.index("Doors open.") < html.index("cid:img2")
    assert 'alt="Studio"' in html

async def test_big_email_images_are_sent_shrunk(files):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "em_1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tools = RealTools(email=Resend("re_x", "Bright <hi@b.co>", http=http), files=files)
    big = await tools.save_file("b1", noise(1600), "image/png", name="studio.png")

    result = await tools.send_email("b1", "list@b.co", "Friday", "Doors open.", [big])

    assert result.ok
    (attachment,) = seen["body"]["attachments"]
    assert attachment["filename"] == "studio.jpg"
    assert len(base64.b64decode(attachment["content"])) <= images.EMAIL_LIMIT

async def test_schedules_due_listed_and_removed_with_the_team(store):
    weekly = Schedule(
        schedule_id="s1",
        business_id="b1",
        team_id="t1",
        title="Monday newsletter idea",
        request="Propose a newsletter",
        cadence=Cadence(every="week", weekday=0),
        created_at=NOW,
    )
    await store.save_schedule(weekly)
    assert [s.schedule_id for s in await store.due_schedules(NOW)] == ["s1"]
    await store.save_schedule(weekly.model_copy(update={"next_run_at": NOW.replace(year=2027)}))
    assert await store.due_schedules(NOW) == []
    assert [s.schedule_id for s in await store.list_schedules("b1", "t1")] == ["s1"]
    assert await store.list_schedules("b1", "t2") == []
    await store.delete_team("t1")
    assert await store.get_schedule("s1") is None
