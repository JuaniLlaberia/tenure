from datetime import timedelta

from brain.common import new_id
from brain.context import MAX_EXAMPLES, MAX_LESSONS, build_context
from contract import BusinessProfile, Lesson, PostSocial

PROFILE = BusinessProfile(
    business_id="b1",
    name="Juan's Studio",
    what_you_sell="Brand design for cafés",
    customers="Independent café owners",
    tone="friendly, no jargon",
)

def make_lesson(team_id, text, created_at, task_type=None):
    return Lesson(
        lesson_id=new_id(),
        business_id="b1",
        team_id=team_id,
        task_type=task_type,
        kind="preference",
        text=text,
        source="chat",
        created_at=created_at,
    )

async def test_context_has_profile_and_lessons(deps, make_team):
    team = await make_team()
    await deps.store.save_profile(PROFILE)
    await deps.store.save_lesson(make_lesson(team.team_id, "No hashtags in posts", deps.clock()))
    await deps.store.save_lesson(make_lesson(None, "We never offer discounts", deps.clock()))

    context = await build_context(deps, "b1", team.team_id, "social_post")

    for text in [
        "Juan's Studio",
        "Brand design for cafés",
        "Independent café owners",
        "friendly, no jargon",
        "No hashtags in posts",
        "We never offer discounts",
    ]:
        assert text in context

async def test_context_caps_lessons_at_twenty_newest_first(deps, make_team, clock):
    team = await make_team()
    for n in range(MAX_LESSONS + 5):
        created = clock() + timedelta(minutes=n)
        await deps.store.save_lesson(make_lesson(team.team_id, f"Lesson {n:02d}", created))

    context = await build_context(deps, "b1", team.team_id, "social_post")

    assert "Lesson 24" in context
    assert "Lesson 05" in context
    assert "Lesson 04" not in context
    assert "Lesson 00" not in context
    assert context.index("Lesson 24") < context.index("Lesson 05")

async def test_context_examples_are_approved_drafts_max_three(
    deps, make_team, make_approval, clock
):
    team = await make_team()
    for n in range(4):
        clock.advance(minutes=1)
        post = PostSocial(text=f"Approved post {n}")
        await make_approval(team, planned_action=post, status="approved")
    clock.advance(minutes=1)
    rejected = await make_approval(team, planned_action=PostSocial(text="Rejected post"))
    await deps.store.save_approval(
        rejected.model_copy(update={"status": "rejected", "resolved_at": clock()})
    )
    clock.advance(minutes=1)
    edited = await make_approval(team, planned_action=PostSocial(text="Original draft"))
    await deps.store.save_approval(
        edited.model_copy(
            update={"status": "edited", "edited_text": "Founder's version", "resolved_at": clock()}
        )
    )

    context = await build_context(deps, "b1", team.team_id, "social_post")

    assert "Founder's version" in context
    assert "Original draft" not in context
    assert "Rejected post" not in context
    assert "Approved post 3" in context
    assert "Approved post 2" in context
    assert "Approved post 1" not in context
    assert MAX_EXAMPLES == 3

async def test_context_includes_prior_outputs_and_feedback(deps, make_team):
    team = await make_team()

    context = await build_context(
        deps,
        "b1",
        team.team_id,
        "newsletter",
        prior_outputs={"researcher": {"notes": "Rivals launch on Mondays", "sources": ["https://a.co"]}},
        feedback=["Mention the Friday date"],
    )

    assert "Rivals launch on Mondays" in context
    assert "https://a.co" in context
    assert "Mention the Friday date" in context

async def test_context_without_profile_still_works(deps, make_team):
    team = await make_team()

    context = await build_context(deps, "b1", team.team_id, "social_post")

    assert isinstance(context, str)
    assert "Juan's Studio" not in context
