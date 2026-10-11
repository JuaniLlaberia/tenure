import re
from pathlib import Path

import pytest
import yaml

from brain.brain import TenureBrain
from brain.context import examples_section
from brain.fakes import FakeImages
from brain.helpers.llm import Completion, ToolCall
from brain.templates.loader import TEMPLATES_DIR, TemplateError, load_templates
from brain.templates.models import Limits
from brain.usage import MeteredImages, MeteredJev, MeteredLLM, UsageLog
from contract import (
    Approval,
    ApprovalDecision,
    Ask,
    Cadence,
    FileKind,
    FileRef,
    Lesson,
    LessonLearned,
    NeedsApproval,
    PostSocial,
    Progress,
    Say,
    Schedule,
    SendEmail,
)

from .conftest import NOW

ALT = "A sunlit shop window on launch day"
PROMPT = "A sunlit shop window on launch day, warm palette, flat style"
WARMER = {
    "lessons": [
        {
            "text": "Use warm colours in images",
            "kind": "preference",
            "business_wide": False,
            "task_type": "visual",
            "replaces": None,
        }
    ]
}

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def system_of(messages):
    return str(messages[0].get("content", "")) if messages else ""

def work(messages):
    """
    Otto calls generate_image once per run; everyone else just finishes.
    """
    otto = "You are Otto" in system_of(messages)
    if otto and not any(m.get("role") == "tool" for m in messages):
        arguments = {"prompt": PROMPT, "aspect_ratio": "1:1", "alt_text": ALT}
        call = ToolCall(id="c1", name="generate_image", arguments=arguments)
        return Completion(text="", tool_calls=[call])
    return Completion(text="Done.")

def made_images(messages):
    replies = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "tool")
    return {"images": re.findall(r"Saved image (\S+):", replies), "caption": "For the launch"}

def runs_of(llm, name):
    """
    The order of specialist runs: one entry per agent call made as `name`, skipping tool rounds.
    """
    return [
        call
        for call in llm.calls
        if call.kind == "complete"
        and f"You are {name}" in system_of(call.messages)
        and not any(m.get("role") == "tool" for m in call.messages)
    ]

def prompt_text(call):
    return "\n".join(str(m.get("content", "")) for m in call.messages)

@pytest.fixture
def images(deps):
    deps.images = FakeImages()
    return deps.images

@pytest.fixture
def campaign(script, llm, images):
    def campaign(task_types=("social_post",), **kwargs):
        script(task_types=task_types, **kwargs)
        llm.completions = work
        llm.structured_responses["ImageOutput"] = made_images

    return campaign

@pytest.fixture
async def marketing(make_team):
    return await make_team()

@pytest.fixture
async def design(make_team):
    return await make_team(template="design")

async def draft(brain, collect, message, team, text="Post about our Friday launch"):
    events = await collect(brain.handle_message(message(team, text)))
    return of(events, NeedsApproval), events

async def reject(brain, collect, needs, reason):
    return await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1", approval_id=needs.approval_id, decision="reject", reason=reason
            )
        )
    )

async def test_without_design_steps_are_unchanged(
    brain, collect, deps, campaign, images, marketing, make_team, message
):
    campaign()
    unready = await make_team(template="design")
    await deps.store.save_team(unready.model_copy(update={"onboarded": False}))

    (needs,), _ = await draft(brain, collect, message, marketing)

    task = await deps.store.get_task(needs.task_id)
    assert task.steps == ["writer"]
    assert needs.planned_action.images == []
    assert images.calls == []

async def test_with_design_the_post_gets_an_illustrator_step(
    brain, collect, deps, campaign, marketing, design, message
):
    campaign()

    (needs,), _ = await draft(brain, collect, message, marketing)

    task = await deps.store.get_task(needs.task_id)
    assert task.steps == ["writer", "design:illustrator"]
    assert task.current_step == 2

def unspecified(jev):
    """
    The request doesn't say whether to make an image; a clear yes in the answer means yes.
    """
    jev.answers["mentions_image"] = 0.1
    jev.answers["wants_image"] = lambda state: 0.9 if "Yes, make an image" in state else 0.1

async def test_asks_for_an_image_when_the_request_doesnt_say(
    brain, collect, deps, jev, campaign, images, marketing, design, message
):
    campaign()
    unspecified(jev)

    events = await collect(brain.handle_message(message(marketing, "Post about our Friday launch")))

    (ask,) = of(events, Ask)
    assert ask.question == "Want Otto to make an image for this?"
    assert ask.quick_replies == ["Yes, make an image", "No image"]
    assert ask.persona.name == "Maya"
    assert not of(events, NeedsApproval) and images.calls == []

async def test_yes_makes_the_image(
    brain, collect, deps, jev, campaign, images, marketing, design, message
):
    campaign()
    unspecified(jev)
    await collect(brain.handle_message(message(marketing, "Post about our Friday launch")))

    events = await collect(brain.handle_message(message(marketing, "Yes, make an image", "m2")))

    (needs,) = of(events, NeedsApproval)
    assert (await deps.store.get_task(needs.task_id)).steps == ["writer", "design:illustrator"]
    assert len(needs.planned_action.images) == 1 and len(images.calls) == 1
    assert deps.store.lessons == {}

@pytest.mark.parametrize("answer", ["No image", "hmm, not sure"])
async def test_no_or_unclear_drops_the_illustrator(
    brain, collect, deps, jev, campaign, images, marketing, design, message, answer
):
    campaign()
    unspecified(jev)
    await collect(brain.handle_message(message(marketing, "Post about our Friday launch")))

    events = await collect(brain.handle_message(message(marketing, answer, "m2")))

    (needs,) = of(events, NeedsApproval)
    assert (await deps.store.get_task(needs.task_id)).steps == ["writer"]
    assert needs.planned_action.images == [] and images.calls == []

async def test_new_image_feedback_never_redraws_a_no_image_draft(
    brain, collect, deps, jev, campaign, images, marketing, design, message
):
    campaign()
    unspecified(jev)
    jev.answers["about_image"] = 0.9
    await collect(brain.handle_message(message(marketing, "Post about our Friday launch")))
    events = await collect(brain.handle_message(message(marketing, "No image", "m2")))
    (needs,) = of(events, NeedsApproval)

    events = await reject(brain, collect, needs, "Use a warmer picture")

    (again,) = of(events, NeedsApproval)
    assert again.planned_action.images == [] and images.calls == []

@pytest.mark.parametrize(
    ("request_text", "wants", "steps"),
    [
        ("Post about Friday with an image", 0.9, ["writer", "design:illustrator"]),
        ("Post about Friday, no image", 0.1, ["writer"]),
    ],
)
async def test_a_request_that_says_never_asks(
    brain, collect, deps, jev, campaign, marketing, design, message, request_text, wants, steps
):
    campaign()
    jev.answers["mentions_image"] = 0.9
    jev.answers["wants_image"] = wants

    events = await collect(brain.handle_message(message(marketing, request_text)))

    assert not of(events, Ask)
    (needs,) = of(events, NeedsApproval)
    assert (await deps.store.get_task(needs.task_id)).steps == steps

async def test_without_design_the_image_question_is_not_asked(
    brain, collect, jev, campaign, marketing, message
):
    campaign()
    unspecified(jev)

    events = await collect(brain.handle_message(message(marketing, "Post about Friday")))

    assert not of(events, Ask)
    assert not any("mentions_image" in questions for _, _, questions in jev.calls)

async def test_a_scheduled_run_never_asks_and_makes_no_unasked_image(
    brain, collect, deps, jev, campaign, images, marketing, design
):
    campaign()
    unspecified(jev)
    schedule = Schedule(
        schedule_id="s1",
        business_id="b1",
        team_id=marketing.team_id,
        title="Monday post",
        request="Write a post about this week's tip",
        cadence=Cadence(every="week", weekday=0),
        created_at=NOW,
    )
    await deps.store.save_schedule(schedule)

    events = await collect(brain.run_schedule("b1", "s1"))

    assert not of(events, Ask)
    (needs,) = of(events, NeedsApproval)
    assert (await deps.store.get_task(needs.task_id)).steps == ["writer"]
    assert images.calls == []

async def test_a_campaign_image_that_fails_is_explained(
    brain, collect, campaign, images, marketing, design, message
):
    campaign()
    images.fail = True

    (needs,), events = await draft(brain, collect, message, marketing)

    assert needs.planned_action.images == []
    (note,) = [say for say in of(events, Say) if "couldn't make the image" in say.text]
    assert note.text == (
        "Otto couldn't make the image this time, so this draft has none. Reject it with a note "
        "about the image to try again."
    )
    assert note.persona.name == "Maya" and events.index(needs) < events.index(note)

async def test_a_made_image_needs_no_note(brain, collect, campaign, marketing, design, message):
    campaign()

    _, events = await draft(brain, collect, message, marketing)

    assert not any("couldn't make the image" in say.text for say in of(events, Say))

async def test_illustrator_uses_the_design_teams_lessons(
    brain, collect, deps, llm, campaign, marketing, design, message
):
    campaign()
    for team, text in [(design, "Warm colours only"), (marketing, "No hashtags in posts")]:
        await deps.store.save_lesson(
            Lesson(
                lesson_id=f"l-{team.template}",
                business_id="b1",
                team_id=team.team_id,
                kind="preference",
                text=text,
                source="chat",
                created_at=NOW,
            )
        )

    await draft(brain, collect, message, marketing)

    (otto,) = runs_of(llm, "Otto")
    assert "Warm colours only" in prompt_text(otto)
    assert "No hashtags in posts" not in prompt_text(otto)
    assert "We launch Friday!" in prompt_text(otto)

async def test_illustrator_progress_shows_in_the_marketing_topic(
    brain, collect, campaign, marketing, design, message
):
    campaign()

    _, events = await draft(brain, collect, message, marketing)

    otto = [e for e in of(events, Progress) if e.persona.name == "Otto"]
    assert otto
    assert all(e.team_id == marketing.team_id for e in otto)

async def test_post_draft_carries_the_image(
    brain, collect, tools, campaign, marketing, design, message
):
    campaign()

    (needs,), _ = await draft(brain, collect, message, marketing)

    (image,) = needs.planned_action.images
    assert (image.source, image.alt_text) == ("generated", ALT)
    assert needs.media == []
    assert needs.persona.name == "Maya"

    await collect(
        brain.resolve_approval(
            ApprovalDecision(business_id="b1", approval_id=needs.approval_id, decision="approve")
        )
    )

    (posted,) = [kwargs for name, kwargs in tools.calls if name == "post_social"]
    assert posted["images"] == [image]

async def test_newsletter_draft_carries_the_image(
    brain, collect, deps, campaign, marketing, design, message
):
    campaign(task_types=("newsletter",))

    (needs,), _ = await draft(brain, collect, message, marketing, "Newsletter about Friday")

    assert isinstance(needs.planned_action, SendEmail)
    assert len(needs.planned_action.images) == 1
    task = await deps.store.get_task(needs.task_id)
    assert task.steps == ["researcher", "writer", "design:illustrator"]

async def test_image_feedback_reruns_only_the_illustrator(
    brain, collect, jev, llm, campaign, marketing, design, message
):
    campaign()
    jev.answers["about_image"] = 0.9
    (needs,), _ = await draft(brain, collect, message, marketing)
    writers, ottos = len(runs_of(llm, "Leo")), len(runs_of(llm, "Otto"))

    events = await reject(brain, collect, needs, "Warmer colours in the image")

    (revised,) = of(events, NeedsApproval)
    assert len(revised.planned_action.images) == 1
    assert len(runs_of(llm, "Leo")) == writers
    assert len(runs_of(llm, "Otto")) == ottos + 1
    assert "Warmer colours in the image" in prompt_text(runs_of(llm, "Otto")[-1])

async def test_text_feedback_reruns_the_writer_and_keeps_the_image(
    brain, collect, jev, llm, images, campaign, marketing, design, message
):
    campaign()
    jev.answers["about_image"] = 0.1
    (needs,), _ = await draft(brain, collect, message, marketing)
    before = len(llm.calls)

    events = await reject(brain, collect, needs, "Mention the 20% discount")

    (again,) = of(events, NeedsApproval)
    assert again.planned_action.images == needs.planned_action.images
    assert len(images.calls) == 1
    rerun = [
        "Leo" if "You are Leo" in system_of(call.messages) else "Otto"
        for call in llm.calls[before:]
        if call.kind == "complete"
        and any(f"You are {n}" in system_of(call.messages) for n in ("Leo", "Otto"))
        and not any(m.get("role") == "tool" for m in call.messages)
    ]
    assert rerun == ["Leo"]

async def test_failed_reviews_never_make_extra_images(
    brain, collect, jev, images, campaign, marketing, design, message
):
    campaign(passes=[0.1, 0.1, 0.9])
    jev.answers["about_image"] = [0.9, 0.1]

    (needs,), _ = await draft(brain, collect, message, marketing)

    assert len(images.calls) == 1
    assert len(needs.planned_action.images) == 1

async def test_a_review_after_a_text_revision_never_redraws(
    brain, collect, jev, llm, images, campaign, marketing, design, message
):
    campaign()
    jev.answers["about_image"] = 0.1
    (needs,), _ = await draft(brain, collect, message, marketing)
    jev.answers["passes_check"] = [0.1, 0.9]
    jev.answers["about_image"] = lambda state: 0.1 if "discount" in state else 0.9

    events = await reject(brain, collect, needs, "Mention the 20% discount")

    assert of(events, NeedsApproval)
    assert len(images.calls) == 1

async def test_check_feedback_about_the_image_reruns_only_the_illustrator(
    brain, collect, jev, llm, campaign, marketing, design, message
):
    campaign(passes=[0.1, 0.9])
    jev.answers["about_image"] = 0.9

    (needs,), _ = await draft(brain, collect, message, marketing)

    assert len(runs_of(llm, "Leo")) == 1
    assert len(runs_of(llm, "Otto")) == 2
    assert len(needs.planned_action.images) == 1

async def test_image_feedback_becomes_a_design_lesson(
    brain, collect, deps, jev, llm, campaign, marketing, design, message
):
    campaign()
    jev.answers["about_image"] = 0.9
    llm.structured_responses["ReflectOutput"] = WARMER
    (needs,), _ = await draft(brain, collect, message, marketing)

    events = await reject(brain, collect, needs, "Warmer colours in the image")

    (learned,) = of(events, LessonLearned)
    assert learned.team_id == design.team_id
    assert learned.persona.name == "Iris"
    lesson = deps.store.lessons[learned.lesson_id]
    assert (lesson.team_id, lesson.task_type, lesson.source) == (design.team_id, "visual", "reject")
    noted = [
        say
        for say in of(events, Say)
        if say.team_id == marketing.team_id and say.persona.name == "Maya"
    ]
    assert any("Iris noted" in say.text and "warm colours" in say.text for say in noted)
    assert events.index(learned) < events.index(of(events, NeedsApproval)[0])

async def test_design_is_suggested_once(brain, collect, deps, campaign, marketing, message):
    campaign()

    first = await collect(brain.handle_message(message(marketing, "Post about Friday")))
    second = await collect(brain.handle_message(message(marketing, "Post about Monday", "m2")))

    assert any("/hire design" in say.text for say in of(first, Say))
    assert not any("/hire design" in say.text for say in of(second, Say))
    assert deps.store.lessons == {}

async def test_images_per_request_are_capped(
    brain, collect, deps, images, campaign, marketing, design, message
):
    campaign(task_types=("social_post", "newsletter"))
    capped = deps.templates["marketing"].model_copy(
        update={"limits": Limits(max_images_per_request=1, token_budget=150000)}
    )
    deps.templates["marketing"] = capped

    approvals, _ = await draft(brain, collect, message, marketing, "Post and newsletter")

    assert len(images.calls) == 1
    assert sum(len(n.planned_action.images) for n in approvals) == 1

async def test_image_cost_stays_with_the_marketing_task(
    deps, store, llm, jev, clock, collect, images, campaign, marketing, design, message
):
    usage_log = UsageLog(store, deps.settings, clock)
    deps.llm = MeteredLLM(llm, usage_log)
    deps.jev = MeteredJev(jev, usage_log)
    images.cost = 0.04
    deps.images = MeteredImages(images, usage_log)
    brain = TenureBrain(deps)
    campaign()

    (needs,), _ = await draft(brain, collect, message, marketing)

    (row,) = [u for u in store.usage if u.model == deps.settings.model_image]
    assert (row.team_id, row.task_id) == (marketing.team_id, needs.task_id)

def test_approved_posts_remember_their_image_in_examples():
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
        task_type="social_post",
        preview="We launch Friday!",
        planned_action=PostSocial(text="We launch Friday!", images=[image]),
        check_confidence=0.9,
        status="approved",
        created_at=NOW,
        resolved_at=NOW,
    )

    assert f"We launch Friday!\nImages: {ALT}" in examples_section([approval])

def copy_templates(tmp_path: Path, edit) -> Path:
    for path in TEMPLATES_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        if data["name"] == "marketing":
            edit(data)
        (tmp_path / path.name).write_text(yaml.safe_dump(data))
    return tmp_path

def test_unknown_with_teams_names_fail_at_load(tmp_path):
    assert load_templates()["marketing"].task_types["social_post"].with_teams == {
        "design": ["illustrator"]
    }

    def unknown_template(data):
        data["task_types"]["social_post"]["with_teams"] = {"video": ["editor"]}

    with pytest.raises(TemplateError, match="video"):
        load_templates(copy_templates(tmp_path / "a", unknown_template))

    def unknown_specialist(data):
        data["task_types"]["social_post"]["with_teams"] = {"design": ["sculptor"]}

    with pytest.raises(TemplateError, match="sculptor"):
        load_templates(copy_templates(tmp_path / "b", unknown_specialist))

@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
