from datetime import UTC, datetime, timedelta

import pytest

from brain.flows.autonomy import (
    gate,
    initial_trust,
    next_level,
    promotion_level,
    record_approve,
    reset_streak,
)
from contract import Approval, AutonomyLevel, Trust

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)

def make_trust(level=AutonomyLevel.ACT_AFTER_APPROVAL, streak=5):
    return Trust(
        team_id="t1", task_type="social_post", level=level, approval_streak=streak, updated_at=NOW
    )

def make_approvals(n=5, confidence=0.9, status="approved"):
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
            status=status,
            created_at=NOW,
            resolved_at=NOW,
        )
        for i in range(n)
    ]

@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (AutonomyLevel.DRAFT_ONLY, "approval"),
        (AutonomyLevel.ACT_AFTER_APPROVAL, "approval"),
        (AutonomyLevel.ACT_AND_REPORT, "act"),
        (AutonomyLevel.AUTONOMOUS, "act_autonomous"),
    ],
)
def test_gate_per_level(level, expected):
    assert gate(level) == expected

def test_next_level_climbs_the_ladder_and_stops_at_the_top():
    assert next_level(AutonomyLevel.DRAFT_ONLY) == AutonomyLevel.ACT_AFTER_APPROVAL
    assert next_level(AutonomyLevel.ACT_AFTER_APPROVAL) == AutonomyLevel.ACT_AND_REPORT
    assert next_level(AutonomyLevel.ACT_AND_REPORT) == AutonomyLevel.AUTONOMOUS
    assert next_level(AutonomyLevel.AUTONOMOUS) is None

def test_record_approve_and_reset_streak():
    trust = make_trust(streak=2)
    approved = record_approve(trust, LATER)
    assert approved.approval_streak == 3
    assert approved.updated_at == LATER
    assert trust.approval_streak == 2

    reset = reset_streak(approved, LATER)
    assert reset.approval_streak == 0
    assert reset.level == trust.level

def test_promotion_offered_at_streak_five():
    level = promotion_level(make_trust(), make_approvals(), AutonomyLevel.AUTONOMOUS, 0.8)
    assert level == AutonomyLevel.ACT_AND_REPORT

def test_no_promotion_below_streak_five():
    trust = make_trust(streak=4)
    assert promotion_level(trust, make_approvals(), AutonomyLevel.AUTONOMOUS, 0.8) is None

def test_no_promotion_with_one_low_confidence():
    approvals = make_approvals(4) + make_approvals(1, confidence=0.79)
    assert promotion_level(make_trust(), approvals, AutonomyLevel.AUTONOMOUS, 0.8) is None

def test_no_promotion_with_an_edit_among_the_last_five():
    approvals = make_approvals(4) + make_approvals(1, status="edited")
    assert promotion_level(make_trust(), approvals, AutonomyLevel.AUTONOMOUS, 0.8) is None

def test_no_promotion_with_fewer_than_five_resolved():
    assert promotion_level(make_trust(), make_approvals(4), AutonomyLevel.AUTONOMOUS, 0.8) is None

def test_no_promotion_at_max_level():
    trust = make_trust(level=AutonomyLevel.ACT_AND_REPORT)
    assert promotion_level(trust, make_approvals(), AutonomyLevel.ACT_AND_REPORT, 0.8) is None

def test_initial_trust_one_row_per_task_type_at_start_level(deps):
    rows = initial_trust("t1", deps.templates["marketing"], NOW)
    assert {row.task_type: row.level for row in rows} == {
        "social_post": AutonomyLevel.ACT_AFTER_APPROVAL,
        "newsletter": AutonomyLevel.ACT_AFTER_APPROVAL,
        "competitor_check": AutonomyLevel.ACT_AND_REPORT,
    }
    assert all(row.approval_streak == 0 and row.team_id == "t1" for row in rows)
