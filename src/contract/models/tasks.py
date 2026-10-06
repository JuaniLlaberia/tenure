from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from contract.models.actions import PlannedAction

class TaskStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    REJECTED = "rejected"
    FAILED = "failed"

class Task(BaseModel):
    task_id: str
    business_id: str
    team_id: str
    task_type: str
    title: str
    brief: str
    status: TaskStatus
    steps: list[str]
    current_step: int = 0
    revisions: int = 0
    tokens_used: int = 0
    created_at: datetime
    updated_at: datetime

class Approval(BaseModel):
    approval_id: str
    business_id: str
    team_id: str
    task_id: str
    task_type: str
    preview: str
    planned_action: PlannedAction | None
    check_confidence: float
    status: Literal["pending", "approved", "edited", "rejected"] = "pending"
    edited_text: str | None = None
    reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
