import base64
import json
import re

import httpx
import pytest

from brain.brain import TenureBrain
from brain.check import hard_validate
from brain.cli import Cli
from brain.context import examples_section
from brain.deps import Deps, Settings
from brain.fakes import PNG, FakeImages, FakeTools, InMemoryStore
from brain.helpers.images import ImageError, OpenRouterImages
from brain.helpers.jev import OpenRouterJev
from brain.helpers.llm import Completion, OpenRouterLLM, ToolCall
from brain.templates.loader import load_templates
from brain.templates.registries import ImageOutput
from brain.tools import BRAIN_TOOLS, MAX_IMAGE_CALLS, PLAN_PREFIX, ToolContext, is_plan
from brain.usage import MeteredImages, MeteredJev, MeteredLLM, UsageLog
from contract import (
    Approval,
    ApprovalDecision,
    Ask,
    AutonomyLevel,
    Error,
    FileKind,
    FileRef,
    LessonLearned,
    NeedsApproval,
    Progress,
    Say,
    TaskStatus,
    TeamHired,
)

from .conftest import NOW

PROMPT = "A fox launching a paper rocket, flat style, deep green and amber, soft light"
ALT = "A fox launching a paper rocket"
CAPTION = "Launch day is Friday!"
WARMER = {
    "lessons": [
        {
            "text": "Use warm colours and no people in images",
            "kind": "preference",
            "business_wide": False,
            "task_type": "visual",
            "replaces": None,
        }
    ]
}

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def has_image_part(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(p.get("type") == "image_url" for p in content):
            return True
    return False

def tool_replies(messages):
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "tool")

def illustrator(messages):
    """
    Calls generate_image once per run, then finishes.
    """
    if any(m.get("role") == "tool" for m in messages):
        return Completion(text="Done.")
    arguments = {"prompt": PROMPT, "aspect_ratio": "1:1", "alt_text": ALT}
    call = ToolCall(id="c1", name="generate_image", arguments=arguments)
    return Completion(text="", tool_calls=[call])

def made_images(messages):
    """
    The final ImageOutput names the images this run saved.
    """
    ids = re.findall(r"Saved image (\S+):", tool_replies(messages))
    return {"images": ids, "caption": CAPTION}

def illustrator_prompts(llm):
    texts = []
    for call in llm.calls:
        text = "\n".join(str(m.get("content", "")) for m in call.messages)
        if call.kind == "complete" and "You are Otto" in text:
            texts.append(text)
    return texts

@pytest.fixture
def images(deps):
    deps.images = FakeImages()
    return deps.images

@pytest.fixture
def design(jev, llm, images):
    """
    Scripts a design request: routes `visual`, the illustrator makes one image, checks pass.
    """
    jev.answers.update(
        {"has_feedback": 0.1, "is_clear": 0.9, "passes_check": 0.9, "needs_visual": 0.9}
    )
    llm.structured_responses["LeadPlan"] = {
        "tasks": [
            {"task_type": "visual", "title": "Launch visual", "brief": "An image for Friday"}
        ],
        "question": None,
    }
    llm.structured_responses["ImageOutput"] = made_images
    llm.structured_responses["CriticReport"] = {"issues": ["Make the colours warmer"]}
    llm.completions = illustrator

@pytest.fixture
async def team(make_team):
    return await make_team(template="design")

async def first_draft(brain, collect, message, team):
    request = message(team, "Make an image for our Friday launch")
    events = await collect(brain.handle_message(request))
    (needs,) = of(events, NeedsApproval)
    return needs, events

async def test_design_template_loads_and_is_hireable(brain, collect):
    info = {t.name: t for t in brain.list_templates()}["design"]

    assert [(p.name, p.role, p.avatar) for p in info.personas] == [
        ("Iris", "Design lead", "design/iris.png"),
        ("Otto", "Illustrator", "design/otto.png"),
    ]
    assert info.task_types == ["visual"]
    template = brain.deps.templates["design"]
    assert template.task_types["visual"].action is None
    assert template.task_types["visual"].start_level == AutonomyLevel.ACT_AFTER_APPROVAL
    assert template.max_level("visual") == AutonomyLevel.ACT_AND_REPORT

    events = await collect(brain.hire_team("b1", "design"))

    assert of(events, TeamHired)[0].display_name == "Design"
    assert isinstance(events[-1], Ask)
    assert "colours" in events[-1].question

async def test_generate_image_only_plans_the_image(deps, tools, images):
    ctx = ToolContext(deps=deps, business_id="b1", team_id="t1", task_type="visual")

    reply = await BRAIN_TOOLS["generate_image"].run(
        ctx, {"prompt": PROMPT, "aspect_ratio": "16:9", "alt_text": ALT}
    )

    assert images.calls == []
    assert not [name for name, _ in tools.calls if name == "save_file"]
    (file,) = ctx.files
    assert is_plan(file) and file.alt_text == ALT
    assert reply.startswith(f"Saved image {file.file_id}: {ALT}")
    assert ctx.prompts == {file.file_id: PROMPT}
    assert ctx.aspects == {file.file_id: "16:9"}

async def test_the_planned_image_is_made_once_after_review(
    brain, collect, deps, tools, images, design, team, message
):
    needs, events = await first_draft(brain, collect, message, team)

    (call,) = images.calls
    assert (call["model"], call["prompt"], call["aspect_ratio"]) == (
        deps.settings.model_image,
        PROMPT,
        "1:1",
    )
    (saved,) = [kwargs for name, kwargs in tools.calls if name == "save_file"]
    assert (saved["business_id"], saved["alt_text"]) == (team.business_id, ALT)
    (image,) = needs.media
    assert not is_plan(image) and await tools.read_file(team.business_id, image.file_id) == PNG
    assert any("is drawing" in e.status for e in of(events, Progress))

async def test_generate_image_is_limited_per_step(deps, images):
    ctx = ToolContext(
        deps=deps, business_id="b1", team_id="t1", task_type="visual", image_calls=MAX_IMAGE_CALLS
    )

    reply = await BRAIN_TOOLS["generate_image"].run(ctx, {"prompt": PROMPT, "alt_text": ALT})

    assert reply.startswith("Tool error")
    assert images.calls == []

async def test_visual_draft_carries_media_not_an_action(
    brain, collect, deps, design, team, message
):
    needs, events = await first_draft(brain, collect, message, team)

    assert needs.planned_action is None
    assert needs.persona.name == "Iris"
    assert needs.preview == CAPTION
    (image,) = needs.media
    assert (image.kind, image.source, image.alt_text) == (FileKind.IMAGE, "generated", ALT)
    approval = await deps.store.get_approval(needs.approval_id)
    assert approval.media == needs.media
    assert any(e.persona.name == "Otto" and "drawing" in e.status for e in of(events, Progress))

    done = await collect(
        brain.resolve_approval(
            ApprovalDecision(business_id="b1", approval_id=needs.approval_id, decision="approve")
        )
    )

    assert not of(done, Error)
    assert (await deps.store.get_task(needs.task_id)).status == TaskStatus.DONE

async def test_visual_at_act_and_report_is_a_say_with_media(
    brain, collect, deps, design, make_team, message
):
    team = await make_team(template="design", levels={"visual": AutonomyLevel.ACT_AND_REPORT})

    events = await collect(brain.handle_message(message(team, "Make an image for Friday")))

    assert not of(events, NeedsApproval)
    delivered = [say for say in of(events, Say) if say.media]
    assert len(delivered) == 1
    assert delivered[0].media[0].alt_text == ALT
    assert CAPTION in delivered[0].text
    task = await deps.store.get_task(delivered[0].task_id)
    assert task.status == TaskStatus.DONE

async def test_image_failure_is_a_tool_error_not_a_crash(
    brain, collect, deps, llm, images, design, team, message
):
    images.fail = True

    events = await collect(brain.handle_message(message(team, "Make an image for Friday")))

    assert not of(events, NeedsApproval)
    errors = of(events, Error)
    assert errors and all(error.recoverable for error in errors)
    task_ids = {e.task_id for e in events if getattr(e, "task_id", None)}
    statuses = [(await deps.store.get_task(task_id)).status for task_id in task_ids]
    assert statuses == [TaskStatus.FAILED]

def test_image_output_needs_one_to_four_known_images():
    made = {
        f"g{n}": FileRef(
            file_id=f"g{n}",
            business_id="b1",
            kind=FileKind.IMAGE,
            mime_type="image/png",
            source="generated",
            created_at=NOW,
        )
        for n in range(5)
    }

    assert hard_validate("image", ImageOutput(images=["g0"], caption="Hi"), made) == []
    assert hard_validate("image", ImageOutput(images=[], caption="Hi"), made)
    assert hard_validate("image", ImageOutput(images=list(made), caption="Hi"), made)
    assert hard_validate("image", ImageOutput(images=["made-up"], caption="Hi"), made)

async def test_review_judges_the_image_prompt_before_any_image_is_made(
    brain, collect, jev, llm, images, tools, design, team, message
):
    jev.answers["passes_check"] = [0.1, 0.1, 0.9]

    await first_draft(brain, collect, message, team)

    critics = [call for call in llm.calls if call.schema and call.schema.__name__ == "CriticReport"]
    assert len(critics) == 2
    assert not any(has_image_part(critic.messages) for critic in critics)
    assert all(PROMPT in str(critic.messages) for critic in critics)
    assert "Make the colours warmer" in illustrator_prompts(llm)[-1]
    assert len(images.calls) == 1
    reads = [kwargs["file_id"] for method, kwargs in tools.calls if method == "read_file"]
    assert not any(file_id.startswith(PLAN_PREFIX) for file_id in reads)

async def test_new_image_makes_exactly_one_more(
    brain, collect, images, tools, design, team, message
):
    needs, _ = await first_draft(brain, collect, message, team)
    decision = ApprovalDecision(
        business_id=team.business_id, approval_id=needs.approval_id, decision="new_image"
    )

    events = await collect(brain.resolve_approval(decision))

    (again,) = of(events, NeedsApproval)
    assert len(images.calls) == 2
    assert again.media[0].file_id != needs.media[0].file_id
    assert not of(events, LessonLearned)

async def test_new_image_with_a_reason_teaches_the_design_team(
    brain, collect, llm, images, design, team, message
):
    llm.structured_responses["ReflectOutput"] = WARMER
    needs, _ = await first_draft(brain, collect, message, team)
    decision = ApprovalDecision(
        business_id=team.business_id,
        approval_id=needs.approval_id,
        decision="new_image",
        reason="Warmer colours, no people",
    )

    events = await collect(brain.resolve_approval(decision))

    assert of(events, LessonLearned) and of(events, NeedsApproval)
    assert "Warmer colours, no people" in illustrator_prompts(llm)[-1]

async def test_new_image_needs_an_image_and_revisions_left(
    brain, collect, deps, images, design, team, message
):
    needs, _ = await first_draft(brain, collect, message, team)
    task = await deps.store.get_task(needs.task_id)
    await deps.store.save_task(task.model_copy(update={"revisions": 2}))
    decision = ApprovalDecision(
        business_id=team.business_id, approval_id=needs.approval_id, decision="new_image"
    )

    (error,) = await collect(brain.resolve_approval(decision))

    assert isinstance(error, Error) and "out of revisions" in error.message
    assert (await deps.store.get_approval(needs.approval_id)).status == "pending"

async def test_image_usage_is_logged_with_its_cost(
    deps, store, llm, jev, clock, collect, design, images, team, message
):
    usage_log = UsageLog(store, deps.settings, clock)
    deps.llm = MeteredLLM(llm, usage_log)
    deps.jev = MeteredJev(jev, usage_log)
    images.cost = 0.04
    deps.images = MeteredImages(images, usage_log)
    brain = TenureBrain(deps)

    needs, _ = await first_draft(brain, collect, message, team)

    (row,) = [u for u in store.usage if u.model == deps.settings.model_image]
    assert (row.purpose, row.cost) == ("Images", 0.04)
    assert (row.team_id, row.task_id) == (team.team_id, needs.task_id)

async def test_reject_with_reason_teaches_the_design_team(
    brain, collect, deps, llm, design, team, message
):
    llm.structured_responses["ReflectOutput"] = WARMER
    needs, _ = await first_draft(brain, collect, message, team)

    events = await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1",
                approval_id=needs.approval_id,
                decision="reject",
                reason="Warmer colours, no people",
            )
        )
    )

    (learned,) = of(events, LessonLearned)
    assert learned.team_id == team.team_id and learned.persona.name == "Iris"
    lesson = deps.store.lessons[learned.lesson_id]
    assert (lesson.team_id, lesson.task_type) == (team.team_id, "visual")
    (revised,) = of(events, NeedsApproval)
    assert revised.media
    assert "Use warm colours and no people in images" in illustrator_prompts(llm)[-1]

def test_approved_visuals_become_examples_with_alt_text():
    image = FileRef(
        file_id="g1",
        business_id="b1",
        kind=FileKind.IMAGE,
        mime_type="image/png",
        source="generated",
        alt_text=ALT,
        created_at=NOW,
    )
    approval = Approval(
        approval_id="a1",
        business_id="b1",
        team_id="t1",
        task_id="k1",
        task_type="visual",
        preview=CAPTION,
        planned_action=None,
        media=[image],
        check_confidence=0.9,
        status="approved",
        created_at=NOW,
        resolved_at=NOW,
    )

    section = examples_section([approval])

    assert CAPTION in section and ALT in section

def test_cli_shows_a_drafts_images(store, brain):
    lines = []
    shell = Cli(brain, store, business_id="b1", out=lines.append)
    image = FileRef(
        file_id="g1",
        business_id="b1",
        kind=FileKind.IMAGE,
        mime_type="image/png",
        source="generated",
        alt_text=ALT,
        created_at=NOW,
    )
    iris = brain.deps.templates["design"].lead.persona

    shell.render(
        NeedsApproval(
            approval_id="a1",
            team_id="t1",
            task_id="k1",
            task_type="visual",
            persona=iris,
            preview=CAPTION,
            planned_action=None,
            media=[image],
            check_confidence=0.9,
        )
    )

    assert CAPTION in lines[0] and ALT in lines[0]

def images_with(responses, requests):
    def handler(request):
        requests.append((request, json.loads(request.content)))
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    return OpenRouterImages(
        Settings(openrouter_api_key="sk-test-key"), transport=httpx.MockTransport(handler)
    )

IMAGE_RESPONSE = {
    "data": [{"b64_json": base64.b64encode(PNG).decode(), "media_type": "image/png"}],
    "usage": {"prompt_tokens": 0, "completion_tokens": 4175, "total_tokens": 4175, "cost": 0.04},
}

async def test_openrouter_images_request_and_response():
    requests = []
    client = images_with([(200, IMAGE_RESPONSE)], requests)

    image = await client.generate("google/gemini-3.1-flash-image", PROMPT, "16:9")

    request, body = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/images"
    assert request.headers["authorization"] == "Bearer sk-test-key"
    assert body == {
        "model": "google/gemini-3.1-flash-image",
        "prompt": PROMPT,
        "aspect_ratio": "16:9",
        "resolution": "1K",
        "n": 1,
    }
    assert (image.data, image.mime_type) == (PNG, "image/png")
    assert (image.usage.output_tokens, image.usage.cost) == (4175, 0.04)

async def test_openrouter_images_retries_once_then_fails():
    requests = []
    client = images_with([(500, {}), (200, IMAGE_RESPONSE)], requests)
    assert (await client.generate("m", PROMPT)).data == PNG

    failing = images_with([(500, {}), (502, {})], [])
    with pytest.raises(ImageError):
        await failing.generate("m", PROMPT)

@pytest.mark.live
async def test_live_generates_a_square_image_under_2mb():
    settings = Settings.from_env()
    if settings.openrouter_api_key is None:
        pytest.skip("OPENROUTER_API_KEY is not set")
    tools = FakeTools()
    deps = Deps(
        store=InMemoryStore(),
        tools=tools,
        llm=OpenRouterLLM(settings),
        jev=OpenRouterJev(settings),
        settings=settings,
        templates=load_templates(),
        images=OpenRouterImages(settings),
    )
    ctx = ToolContext(deps=deps, business_id="b1", team_id="t1", task_type="visual")

    reply = await BRAIN_TOOLS["generate_image"].run(
        ctx, {"prompt": PROMPT, "aspect_ratio": "1:1", "alt_text": ALT}
    )

    assert reply.startswith("Saved image")
    (file,) = ctx.files
    data = await tools.read_file("b1", file.file_id)
    assert 0 < len(data) < 2_000_000
