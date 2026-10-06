from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

class AutonomyLevel(StrEnum):
    DRAFT_ONLY = "draft_only"
    ACT_AFTER_APPROVAL = "act_after_approval"
    ACT_AND_REPORT = "act_and_report"
    AUTONOMOUS = "autonomous"

class Trust(BaseModel):
    team_id: str
    task_type: str
    level: AutonomyLevel
    approval_streak: int = 0
    updated_at: datetime
