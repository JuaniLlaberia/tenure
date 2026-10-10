from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from contract.models.actions import PlannedAction
from contract.models.files import FileRef

class IncomingMessage(BaseModel):
    business_id: str
    team_id: str | None
    text: str
    message_id: str
    sent_at: datetime
    attachments: list[FileRef] = []

class ApprovalDecision(BaseModel):
    business_id: str
    approval_id: str
    decision: Literal["approve", "edit", "reject", "new_image"]
    edited_text: str | None = None
    edited_action: PlannedAction | None = None
    reason: str | None = None

class PromotionResponse(BaseModel):
    business_id: str
    team_id: str
    task_type: str
    accepted: bool
