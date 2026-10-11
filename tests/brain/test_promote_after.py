from datetime import UTC, datetime

from brain.flows.approvals import resolve_approval
from brain.flows.autonomy import initial_trust, promotion_level
from brain.flows.promotion import respond_promotion
from contract import (
    Approval,
    ApprovalDecision,
    AutonomyLevel,
    PromotionOffer,
    PromotionResponse,
    Trust,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

def make_trust(streak, promote_after):
    return Trust(
        team_id="t1",
        task_type="social_post",
        level=AutonomyLevel.ACT_AFTER_APPROVAL,
        approval_streak=streak,
        promote_after=promote_after,
        updated_at=NOW,
    )

def approvals(confidences):
    return [
        Approval(
            approval_id=f"a{i}",
            business_id="b1",
            team_id="t1",
            task_id=f"k{i}",
            task_type="social_post",
            preview="Post",
            planned_action=None,
            check_confidence=confidence,
            status="approved",
            created_at=NOW,
            resolved_at=NOW,
        )
        for i, confidence in enumerate(confidences)
    ]

def approve(approval):
    return ApprovalDecision(
        business_id=approval.business_id, approval_id=approval.approval_id, decision="approve"
    )

def test_promotion_follows_promote_after():
    trust = make_trust(streak=3, promote_after=3)
    level = promotion_level(trust, approvals([0.9] * 3), AutonomyLevel.AUTONOMOUS, 0.8)
    assert level == AutonomyLevel.ACT_AND_REPORT

def test_no_promotion_below_custom_streak():
    trust = make_trust(streak=2, promote_after=3)
    assert promotion_level(trust, approvals([0.9] * 3), AutonomyLevel.AUTONOMOUS, 0.8) is None

def test_only_the_last_promote_after_approvals_count():
    trust = make_trust(streak=3, promote_after=3)
    recent = approvals([0.9, 0.9, 0.9, 0.2, 0.2])
    assert promotion_level(trust, recent, AutonomyLevel.AUTONOMOUS, 0.8) is not None

def test_promote_after_of_one_promotes_on_the_first_clean_approval():
    trust = make_trust(streak=1, promote_after=1)
    level = promotion_level(trust, approvals([0.85]), AutonomyLevel.AUTONOMOUS, 0.8)
    assert level == AutonomyLevel.ACT_AND_REPORT

def test_initial_trust_uses_the_contract_default(deps):
    rows = initial_trust("t1", deps.templates["marketing"], NOW)
    assert all(row.promote_after == 5 for row in rows)

async def test_approve_offers_promotion_at_custom_threshold(
    deps, collect, make_team, make_approval
):
    team = await make_team(streaks={"social_post": 2}, promote_after={"social_post": 3})
    for _ in range(2):
        await make_approval(team, status="approved")
    approval = await make_approval(team)

    events = await collect(resolve_approval(deps, approve(approval)))

    offer = events[-1]
    assert isinstance(offer, PromotionOffer)
    assert "3" in offer.evidence
    assert "5" not in offer.evidence

async def test_approve_does_not_offer_below_custom_threshold(
    deps, collect, make_team, make_approval
):
    team = await make_team(streaks={"social_post": 1}, promote_after={"social_post": 3})
    await make_approval(team, status="approved")
    approval = await make_approval(team)

    events = await collect(resolve_approval(deps, approve(approval)))

    assert not any(isinstance(e, PromotionOffer) for e in events)

async def test_flows_keep_promote_after(deps, collect, make_team, make_approval):
    team = await make_team(promote_after={"social_post": 3})
    await collect(resolve_approval(deps, approve(await make_approval(team))))
    assert (await deps.store.get_trust(team.team_id, "social_post")).promote_after == 3

    response = PromotionResponse(
        business_id="b1", team_id=team.team_id, task_type="social_post", accepted=False
    )
    await collect(respond_promotion(deps, response))
    assert (await deps.store.get_trust(team.team_id, "social_post")).promote_after == 3
