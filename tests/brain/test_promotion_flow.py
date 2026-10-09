from brain.flows.approvals import resolve_approval
from brain.flows.promotion import respond_promotion
from contract import (
    ApprovalDecision,
    AutonomyLevel,
    Error,
    PromotionOffer,
    PromotionResponse,
    Say,
)

def respond(team, accepted, task_type="social_post", business_id="b1"):
    return PromotionResponse(
        business_id=business_id, team_id=team.team_id, task_type=task_type, accepted=accepted
    )

def approve(approval):
    return ApprovalDecision(
        business_id=approval.business_id, approval_id=approval.approval_id, decision="approve"
    )

async def test_accept_moves_up_one_level_and_resets_streak(deps, collect, make_team):
    team = await make_team(streaks={"social_post": 5})

    events = await collect(respond_promotion(deps, respond(team, accepted=True)))

    assert [type(e) for e in events] == [Say]
    assert events[0].persona.name == "Maya"
    trust = await deps.store.get_trust(team.team_id, "social_post")
    assert trust.level == AutonomyLevel.ACT_AND_REPORT
    assert trust.approval_streak == 0

async def test_decline_resets_streak_and_keeps_level(deps, collect, make_team):
    team = await make_team(streaks={"social_post": 5})

    events = await collect(respond_promotion(deps, respond(team, accepted=False)))

    assert [type(e) for e in events] == [Say]
    trust = await deps.store.get_trust(team.team_id, "social_post")
    assert trust.level == AutonomyLevel.ACT_AFTER_APPROVAL
    assert trust.approval_streak == 0

async def test_decline_then_next_approve_does_not_reoffer(
    deps, collect, make_team, make_approval
):
    team = await make_team(streaks={"social_post": 4})
    for _ in range(4):
        await make_approval(team, status="approved")
    first = await collect(resolve_approval(deps, approve(await make_approval(team))))
    assert isinstance(first[-1], PromotionOffer)

    await collect(respond_promotion(deps, respond(team, accepted=False)))
    events = await collect(resolve_approval(deps, approve(await make_approval(team))))

    assert not any(isinstance(e, PromotionOffer) for e in events)

async def test_accept_at_max_level_keeps_level(deps, collect, make_team):
    team = await make_team(levels={"newsletter": AutonomyLevel.ACT_AND_REPORT})

    events = await collect(
        respond_promotion(deps, respond(team, accepted=True, task_type="newsletter"))
    )

    assert [type(e) for e in events] == [Say]
    trust = await deps.store.get_trust(team.team_id, "newsletter")
    assert trust.level == AutonomyLevel.ACT_AND_REPORT

async def test_missing_trust_yields_error(deps, collect, make_team):
    team = await make_team()

    events = await collect(
        respond_promotion(deps, respond(team, accepted=True, task_type="invoice_reminder"))
    )

    assert [type(e) for e in events] == [Error]
    assert events[0].recoverable

async def test_other_business_yields_error(deps, collect, make_team):
    team = await make_team(streaks={"social_post": 5})

    events = await collect(respond_promotion(deps, respond(team, True, business_id="b2")))

    assert [type(e) for e in events] == [Error]
    trust = await deps.store.get_trust(team.team_id, "social_post")
    assert trust.level == AutonomyLevel.ACT_AFTER_APPROVAL
