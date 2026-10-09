import base64
from pathlib import Path

import pytest

from brain.brain import TenureBrain
from brain.check import hard_validate
from brain.cli import Cli
from brain.deps import Deps, Settings
from brain.fakes import FakeTools, InMemoryStore
from brain.helpers.jev import OpenRouterJev
from brain.helpers.llm import Completion, OpenRouterLLM, Usage
from brain.media import UNHEARD_REPLY, describe
from brain.prompts.chief_of_staff import Extraction
from brain.templates.loader import load_templates
from brain.templates.registries import OUTPUTS, EmailOutput, SocialPostOutput
from brain.usage import MeteredJev, MeteredLLM, UsageLog
from contract import (
    ApprovalDecision,
    Error,
    FileKind,
    FileRef,
    IncomingMessage,
    NeedsApproval,
    PostSocial,
    Progress,
    Say,
)

from .conftest import NOW

TRANSCRIPT = "We launch Friday, get the word out"
STUDIO = "A sunlit studio with plants on the shelves"
PRICES = "Logo package: $2,500. Brand refresh: $6,000."
MEDIA_PARTS = ("input_audio", "image_url", "file")
FIXTURE = Path(__file__).parent / "fixtures" / "voice_note.ogg"

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def media_parts(messages):
    parts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts.extend(p for p in content if p.get("type") in MEDIA_PARTS)
    return parts

def media_calls(llm):
    return [call for call in llm.calls if media_parts(call.messages)]

def answer_media(llm, usage=None, **answers):
    """
    Scripts the media model: the answer for the first media part's type (audio, image, file);
    every other completion is a plain "Done.".
    """
    kinds = {"audio": "input_audio", "image": "image_url", "file": "file"}
    by_part = {kinds[kind]: text for kind, text in answers.items()}

    def complete(messages):
        parts = media_parts(messages)
        if parts:
            return Completion(text=by_part[parts[0]["type"]], usage=usage or Usage())
        return Completion(text="Done.")

    llm.completions = complete

def founder_file(tools, file_id, kind, mime_type, data=b"bytes", name=None, business_id="b1"):
    ref = FileRef(
        file_id=file_id,
        business_id=business_id,
        kind=kind,
        mime_type=mime_type,
        name=name,
        size_bytes=len(data),
        source="founder",
        created_at=NOW,
    )
    tools.add_file(ref, data)
    return ref

def photo(tools, file_id="f1"):
    return founder_file(tools, file_id, FileKind.IMAGE, "image/jpeg", b"\xff\xd8jpeg")

def voice(tools):
    return founder_file(tools, "v1", FileKind.AUDIO, "audio/ogg", b"OggS voice")

def with_files(msg, *files):
    return msg.model_copy(update={"attachments": list(files)})

def triage_state(jev):
    return next(state for _, state, questions in jev.calls if "has_feedback" in questions)

def writer_prompts(llm):
    texts = []
    for call in llm.calls:
        if call.kind != "complete" or media_parts(call.messages):
            continue
        text = "\n".join(str(m.get("content", "")) for m in call.messages)
        if "You are Leo" in text:
            texts.append(text)
    return texts

@pytest.fixture
async def team(make_team):
    return await make_team()

async def test_voice_note_becomes_the_request(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    answer_media(llm, audio=TRANSCRIPT)
    note = voice(tools)

    events = await collect(brain.handle_message(with_files(message(team, ""), note)))

    assert of(events, NeedsApproval)
    assert not of(events, Error)
    assert TRANSCRIPT in triage_state(jev)
    assert "[Voice note]" not in triage_state(jev)
    (part,) = media_parts(media_calls(llm)[0].messages)
    assert part["input_audio"]["format"] == "ogg"
    assert base64.b64decode(part["input_audio"]["data"]) == b"OggS voice"
    assert media_calls(llm)[0].model == brain.deps.settings.model_media
    assert media_calls(llm)[0].reasoning is None
    statuses = [e.status for e in of(events, Progress)]
    assert "Maya is listening to your voice note…" in statuses

async def test_caption_and_transcript_are_joined(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    answer_media(llm, audio=TRANSCRIPT)
    note = voice(tools)

    await collect(brain.handle_message(with_files(message(team, "For the newsletter:"), note)))

    state = triage_state(jev)
    assert "For the newsletter:" in state
    assert f"[Voice note] {TRANSCRIPT}" in state
    assert state.index("For the newsletter:") < state.index("[Voice note]")

async def test_photo_is_described_and_offered_to_the_writer(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    answer_media(llm, image=STUDIO)
    studio = photo(tools)

    events = await collect(
        brain.handle_message(with_files(message(team, "Post this about our new studio"), studio))
    )

    (part,) = media_parts(media_calls(llm)[0].messages)
    assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert f"[Photo f1: {STUDIO}]" in triage_state(jev)
    assert "Maya is looking at your photo…" in [e.status for e in of(events, Progress)]
    prompt = writer_prompts(llm)[0]
    assert "f1" in prompt and STUDIO in prompt

async def test_writer_can_attach_the_founders_photo(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    llm.structured_responses["SocialPostOutput"] = {"text": "Our new studio!", "images": ["f1"]}
    answer_media(llm, image=STUDIO)
    studio = photo(tools)

    events = await collect(
        brain.handle_message(with_files(message(team, "Post this about our new studio"), studio))
    )

    (needs,) = of(events, NeedsApproval)
    (attached,) = needs.planned_action.images
    assert attached.file_id == "f1"
    assert attached.alt_text == STUDIO
    assert needs.media == []
    check = next(state for _, state, questions in jev.calls if "passes_check" in questions)
    assert f"With 1 photo: {STUDIO}" in check

    await collect(
        brain.resolve_approval(
            ApprovalDecision(business_id="b1", approval_id=needs.approval_id, decision="approve")
        )
    )

    (posted,) = [kwargs for name, kwargs in tools.calls if name == "post_social"]
    assert posted["images"] == [attached]

def test_unknown_image_ids_are_dropped():
    studio = FileRef(
        file_id="f1",
        business_id="b1",
        kind=FileKind.IMAGE,
        mime_type="image/jpeg",
        source="founder",
        created_at=NOW,
    )
    output = SocialPostOutput(text="Hi", images=["f1", "made-up", "f1"])

    planned = OUTPUTS["social_post"].to_planned_action(output, "post_social", {"f1": studio})

    assert planned == PostSocial(text="Hi", images=[studio])

def test_more_than_four_images_fail_the_hard_check():
    ids = [f"f{n}" for n in range(5)]
    post = SocialPostOutput(text="Hi", images=ids)
    email = EmailOutput(to="list@b.co", subject="Hi", body="Hello", images=ids)

    assert any("4" in error for error in hard_validate("social_post", post))
    assert any("4" in error for error in hard_validate("email", email))
    assert hard_validate("social_post", SocialPostOutput(text="Hi", images=ids[:4])) == []

async def test_pdf_is_read_as_context(brain, collect, llm, tools, script, team, message):
    script()
    answer_media(llm, file=PRICES)
    prices = founder_file(
        tools, "d1", FileKind.DOCUMENT, "application/pdf", b"%PDF-1.7", name="price-list.pdf"
    )

    events = await collect(
        brain.handle_message(with_files(message(team, "Post about our prices"), prices))
    )

    (part,) = media_parts(media_calls(llm)[0].messages)
    assert part["file"]["filename"] == "price-list.pdf"
    assert part["file"]["file_data"].startswith("data:application/pdf;base64,")
    plan = next(call for call in llm.calls if call.schema and call.schema.__name__ == "LeadPlan")
    plan_text = str(plan.messages)
    assert "[Document price-list.pdf]" in plan_text and PRICES in plan_text
    assert "Maya is reading your document…" in [e.status for e in of(events, Progress)]

async def test_text_document_needs_no_model(brain, collect, jev, llm, tools, script, team, message):
    script()
    notes = founder_file(
        tools, "d2", FileKind.DOCUMENT, "text/plain", b"Launch is on Friday", name="notes.txt"
    )

    await collect(brain.handle_message(with_files(message(team, "Post about this"), notes)))

    assert media_calls(llm) == []
    assert "[Document notes.txt]\nLaunch is on Friday" in triage_state(jev)

async def test_video_gets_a_polite_say(brain, collect, jev, llm, tools, team, message):
    clip = founder_file(tools, "c1", FileKind.VIDEO, "video/mp4", b"video")

    events = await collect(brain.handle_message(with_files(message(team, ""), clip)))

    (say,) = of(events, Say)
    assert "can't watch videos" in say.text
    assert say.persona.name == "Maya"
    assert not of(events, Error)
    assert jev.calls == [] and llm.calls == []
    assert all(name != "read_file" for name, _ in tools.calls)

async def test_unreadable_file_does_not_stop_the_run(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    missing = FileRef(
        file_id="f9",
        business_id="b1",
        kind=FileKind.IMAGE,
        mime_type="image/png",
        source="founder",
        created_at=NOW,
    )

    events = await collect(
        brain.handle_message(with_files(message(team, "Post about Friday"), missing))
    )

    assert not of(events, Error)
    assert of(events, NeedsApproval)
    assert "[Photo f9: couldn't open it]" in triage_state(jev)
    assert media_calls(llm) == []

def fail_media(llm):
    """
    The media model refuses every file, as OpenRouter did when reasoning was turned off.
    """

    def complete(messages):
        if media_parts(messages):
            raise RuntimeError("400 Bad Request")
        return Completion(text="Done.")

    llm.completions = complete

async def test_an_unheard_voice_note_is_said_and_starts_nothing(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    fail_media(llm)
    note = voice(tools)

    events = await collect(brain.handle_message(with_files(message(team, ""), note)))

    assert [e.text for e in of(events, Say)] == [UNHEARD_REPLY]
    assert not of(events, NeedsApproval) and not of(events, Error)
    assert not [questions for _, _, questions in jev.calls if "has_feedback" in questions]

async def test_an_unheard_voice_note_with_a_caption_still_runs(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    fail_media(llm)
    note = voice(tools)

    events = await collect(
        brain.handle_message(with_files(message(team, "Post about Friday"), note))
    )

    assert UNHEARD_REPLY in [e.text for e in of(events, Say)]
    assert of(events, NeedsApproval)
    assert "Post about Friday" in triage_state(jev)

async def test_an_unheard_voice_note_in_onboarding_is_not_an_answer(
    brain, collect, llm, tools
):
    fail_media(llm)
    await collect(brain.start_onboarding("b1"))
    note = voice(tools)
    msg = IncomingMessage(
        business_id="b1", team_id=None, text="", message_id="m1", sent_at=NOW, attachments=[note]
    )

    events = await collect(brain.handle_message(msg))

    assert [e.text for e in of(events, Say)] == [UNHEARD_REPLY]
    assert not [call for call in llm.calls if call.schema is Extraction]

async def test_another_business_file_is_not_read(
    brain, collect, jev, llm, tools, script, team, message
):
    script()
    answer_media(llm, image=STUDIO)
    foreign = founder_file(
        tools, "f1", FileKind.IMAGE, "image/jpeg", b"\xff\xd8", business_id="other"
    )

    await collect(brain.handle_message(with_files(message(team, "Post this"), foreign)))

    assert media_calls(llm) == []
    assert "[Photo f1: couldn't open it]" in triage_state(jev)

async def test_media_survives_a_revision(brain, collect, llm, tools, script, team, message):
    script()
    llm.structured_responses["SocialPostOutput"] = {"text": "Our new studio!", "images": ["f1"]}
    answer_media(llm, image=STUDIO)
    studio = photo(tools)
    first = await collect(
        brain.handle_message(with_files(message(team, "Post this about our new studio"), studio))
    )
    (needs,) = of(first, NeedsApproval)
    await collect(brain.handle_message(message(team, "Unrelated: how are you?", "m2")))

    events = await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1",
                approval_id=needs.approval_id,
                decision="reject",
                reason="Shorter please",
            )
        )
    )

    (revised,) = of(events, NeedsApproval)
    assert [ref.file_id for ref in revised.planned_action.images] == ["f1"]
    last = writer_prompts(llm)[-1]
    assert "Shorter please" in last and STUDIO in last

async def test_edit_keeps_the_founders_photo(brain, collect, llm, tools, script, team, message):
    script()
    llm.structured_responses["SocialPostOutput"] = {"text": "Our new studio!", "images": ["f1"]}
    answer_media(llm, image=STUDIO)
    studio = photo(tools)
    events = await collect(
        brain.handle_message(with_files(message(team, "Post this about our new studio"), studio))
    )
    (needs,) = of(events, NeedsApproval)

    await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1",
                approval_id=needs.approval_id,
                decision="edit",
                edited_text="Come see the new studio.",
            )
        )
    )

    (posted,) = [kwargs for name, kwargs in tools.calls if name == "post_social"]
    assert posted["text"] == "Come see the new studio."
    assert [ref.file_id for ref in posted["images"]] == ["f1"]

async def test_onboarding_reads_a_document(brain, collect, deps, jev, llm, tools):
    jev.answers["answer_clear"] = 0.9
    answer_media(llm, file="Juan's Studio designs logos for independent cafés.")
    llm.structured_responses["Extraction"] = {
        "fields": {
            "name": "Juan's Studio",
            "what_you_sell": "Logos",
            "customers": "Independent cafés",
            "tone": "warm",
        },
        "facts": [],
    }
    deck = founder_file(
        tools, "d1", FileKind.DOCUMENT, "application/pdf", b"%PDF-1.7", name="about-us.pdf"
    )
    await collect(brain.start_onboarding("b1"))

    events = await collect(
        brain.handle_message(
            IncomingMessage(
                business_id="b1",
                team_id=None,
                text="",
                message_id="m1",
                sent_at=NOW,
                attachments=[deck],
            )
        )
    )

    extract = next(call for call in llm.calls if call.schema is Extraction)
    prompt = str(extract.messages)
    assert "about-us.pdf" in prompt
    assert "Juan's Studio designs logos for independent cafés." in prompt
    assert "Alex is reading your document…" in [e.status for e in of(events, Progress)]
    profile = await deps.store.get_profile("b1")
    assert profile is not None and profile.what_you_sell == "Logos"

async def test_media_usage_is_logged_with_its_purpose(
    deps, store, llm, jev, clock, collect, tools, script, make_team, message
):
    usage_log = UsageLog(store, deps.settings, clock)
    deps.llm = MeteredLLM(llm, usage_log)
    deps.jev = MeteredJev(jev, usage_log)
    brain = TenureBrain(deps)
    team = await make_team()
    script()
    answer_media(llm, usage=Usage(input_tokens=40, output_tokens=8, cost=0.0002), audio=TRANSCRIPT)

    await collect(brain.handle_message(with_files(message(team, ""), voice(tools))))

    (row,) = [u for u in store.usage if u.model == deps.settings.model_media]
    assert row.purpose == "Listening and reading"
    assert (row.business_id, row.team_id) == ("b1", team.team_id)
    assert row.cost == 0.0002

async def test_fake_tools_keep_files_per_business():
    tools = FakeTools()
    founder_file(tools, "f1", FileKind.IMAGE, "image/png", b"png")

    saved = await tools.save_file("b1", b"made", "image/png", alt_text="A fox with a megaphone")

    assert await tools.read_file("b1", "f1") == b"png"
    assert await tools.read_file("other", "f1") is None
    assert (saved.source, saved.kind, saved.alt_text) == (
        "generated",
        FileKind.IMAGE,
        "A fox with a megaphone",
    )
    assert await tools.read_file("b1", saved.file_id) == b"made"

async def test_cli_attaches_a_file_to_the_next_message(store, tools, tmp_path):
    sent = []

    class Recording:
        def handle_message(self, msg):
            sent.append(msg)
            return _nothing()

    shot = tmp_path / "studio.png"
    shot.write_bytes(b"png bytes")
    shell = Cli(Recording(), store, business_id="b1", out=lambda _: None, tools=tools)

    await shell.handle(f"/file {shot}")
    await shell.handle("")
    await shell.handle("Thanks")

    first, second = sent
    (file,) = first.attachments
    assert (first.text, file.kind, file.mime_type, file.name) == (
        "",
        FileKind.IMAGE,
        "image/png",
        "studio.png",
    )
    assert await tools.read_file("b1", file.file_id) == b"png bytes"
    assert second.attachments == []

async def _nothing():
    return
    yield

@pytest.mark.live
async def test_live_transcribes_a_telegram_voice_note():
    settings = Settings.from_env()
    if settings.openrouter_api_key is None:
        pytest.skip("OPENROUTER_API_KEY is not set")
    if not FIXTURE.exists():
        pytest.skip(f"No voice note fixture at {FIXTURE}")
    tools = FakeTools()
    data = FIXTURE.read_bytes()
    note = founder_file(tools, "v1", FileKind.AUDIO, "audio/ogg", data)
    deps = Deps(
        store=InMemoryStore(),
        tools=tools,
        llm=OpenRouterLLM(settings),
        jev=OpenRouterJev(settings),
        settings=settings,
        templates=load_templates(),
    )

    described = await describe(deps, "b1", note)

    assert "friday" in described.text.lower()
