from brain.cli import seed_approvals
from contract import (
    ActionDone,
    ApprovalDecision,
    Ask,
    AutonomyLevel,
    IncomingMessage,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    PromotionOffer,
    PromotionResponse,
    TeamHired,
)

PROFILE = {
    "name": "Juan's Studio",
    "what_you_sell": "Brand design for cafés",
    "customers": "Independent cafés",
    "tone": "warm",
}
LESSON = {
    "text": "Sign posts as Juan",
    "kind": "preference",
    "business_wide": False,
    "task_type": "social_post",
    "replaces": None,
}

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def say(team_id, text, n):
    return IncomingMessage(
        business_id="b1",
        team_id=team_id,
        text=text,
        message_id=f"m{n}",
        sent_at="2026-10-08T12:00:00Z",
    )

def decide(approval_id, decision, **fields):
    return ApprovalDecision(business_id="b1", approval_id=approval_id, decision=decision, **fields)

async def test_demo_script(brain, collect, deps, jev, llm, script, clock):
    script(task_types=("social_post", "newsletter"))
    jev.answers["answer_clear"] = 0.9
    llm.structured_responses["Extraction"] = {"fields": PROFILE, "facts": []}
    llm.structured_responses["ReflectOutput"] = {"lessons": [LESSON]}

    # 1. business onboarding
    await collect(brain.start_onboarding("b1"))
    site = say(None, "Here's our site: https://juans.studio", 1)
    asked = await collect(brain.handle_message(site))
    assert of(asked, Ask)[-1].quick_replies == ["Skip (Los Angeles time)"]
    done = await collect(brain.handle_message(say(None, "Skip (Los Angeles time)", 9)))
    assert of(done, OnboardingComplete)[0].scope == "business"
    assert (await deps.store.get_profile("b1")).name == "Juan's Studio"

    # 2. hire marketing and answer its three questions
    hired = await collect(brain.hire_team("b1", "marketing"))
    team_id = of(hired, TeamHired)[0].team_id
    for n, answer in enumerate(["Bluesky", "We launch Friday", "list@b.co"], start=2):
        answered = await collect(brain.handle_message(say(team_id, answer, n)))
    assert of(answered, OnboardingComplete)[0].scope == "team"

    # 3. one request, two drafts
    request = say(team_id, "We launch Friday, get the word out", 5)
    work = await collect(brain.handle_message(request))
    drafts = {d.task_type: d for d in of(work, NeedsApproval)}
    assert set(drafts) == {"social_post", "newsletter"}

    # 4. edit the post: it's posted and the team learns
    edited = await collect(
        brain.resolve_approval(
            decide(drafts["social_post"].approval_id, "edit", edited_text="Friday. — Juan")
        )
    )
    assert of(edited, ActionDone)[0].undo_until is not None
    assert of(edited, LessonLearned)[0].text == "Sign posts as Juan"

    # 5. approve the newsletter: sent, never undoable
    newsletter = decide(drafts["newsletter"].approval_id, "approve")
    sent = await collect(brain.resolve_approval(newsletter))
    assert of(sent, ActionDone)[0].undo_until is None

    # 6. seeded history (we say so in the demo), one more clean approval → promotion
    clock.advance(minutes=1)
    await seed_approvals(deps.store, team_id, "social_post", 4, clock())
    clock.advance(minutes=1)
    script(task_types=("social_post",))
    work = await collect(brain.handle_message(say(team_id, "Post a Friday reminder", 6)))
    post = of(work, NeedsApproval)[0]
    approved = await collect(brain.resolve_approval(decide(post.approval_id, "approve")))
    offer = of(approved, PromotionOffer)[0]
    assert offer.proposed_level == AutonomyLevel.ACT_AND_REPORT

    await collect(
        brain.respond_promotion(
            PromotionResponse(
                business_id="b1", team_id=team_id, task_type="social_post", accepted=True
            )
        )
    )
    trust = await deps.store.get_trust(team_id, "social_post")
    assert trust.level == AutonomyLevel.ACT_AND_REPORT

    # 7. the next post goes out without asking
    clock.advance(minutes=1)
    work = await collect(brain.handle_message(say(team_id, "Post that doors open at 6", 7)))
    assert not of(work, NeedsApproval)
    assert of(work, ActionDone)

async def test_seed_approvals_sets_streak_and_history(deps, make_team, clock):
    team = await make_team()

    await seed_approvals(deps.store, team.team_id, "social_post", 4, clock())

    trust = await deps.store.get_trust(team.team_id, "social_post")
    assert trust.approval_streak == 4
    recent = await deps.store.recent_approvals(team.team_id, "social_post", 10)
    assert len(recent) == 4
    assert all(a.status == "approved" and a.check_confidence >= 0.8 for a in recent)
