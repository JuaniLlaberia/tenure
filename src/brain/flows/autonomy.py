from datetime import datetime, timedelta
from typing import Literal

from brain.templates.models import LADDER, Template
from contract import Approval, AutonomyLevel, Trust

STREAK_FOR_PROMOTION = 5
UNDO_WINDOW = timedelta(minutes=10)

Gate = Literal["approval", "act", "act_autonomous"]

GATES: dict[AutonomyLevel, Gate] = {
    AutonomyLevel.DRAFT_ONLY: "approval",
    AutonomyLevel.ACT_AFTER_APPROVAL: "approval",
    AutonomyLevel.ACT_AND_REPORT: "act",
    AutonomyLevel.AUTONOMOUS: "act_autonomous",
}

def gate(level: AutonomyLevel) -> Gate:
    return GATES[level]

def next_level(level: AutonomyLevel) -> AutonomyLevel | None:
    index = LADDER.index(level) + 1
    return LADDER[index] if index < len(LADDER) else None

def is_above(level: AutonomyLevel, cap: AutonomyLevel) -> bool:
    return LADDER.index(level) > LADDER.index(cap)

def record_approve(trust: Trust, now: datetime) -> Trust:
    streak = trust.approval_streak + 1
    return trust.model_copy(update={"approval_streak": streak, "updated_at": now})

def reset_streak(trust: Trust, now: datetime) -> Trust:
    return trust.model_copy(update={"approval_streak": 0, "updated_at": now})

def promotion_level(
    trust: Trust, recent: list[Approval], max_level: AutonomyLevel, min_confidence: float
) -> AutonomyLevel | None:
    """
    The level to offer, or None. Needs a streak of 5 and the last 5 resolved approvals all
    approved without edits at check confidence >= min_confidence, below the template's cap.
    """
    proposed = next_level(trust.level)
    if proposed is None or is_above(proposed, max_level):
        return None
    if trust.approval_streak < STREAK_FOR_PROMOTION:
        return None
    last = recent[:STREAK_FOR_PROMOTION]
    if len(last) < STREAK_FOR_PROMOTION:
        return None
    if any(a.status != "approved" or a.check_confidence < min_confidence for a in last):
        return None
    return proposed

def initial_trust(team_id: str, template: Template, now: datetime) -> list[Trust]:
    return [
        Trust(team_id=team_id, task_type=task_type, level=spec.start_level, updated_at=now)
        for task_type, spec in template.task_types.items()
    ]
