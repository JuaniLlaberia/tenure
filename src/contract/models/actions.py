from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from contract.models.files import FileRef

class PostSocial(BaseModel):
    tool: Literal["post_social"] = "post_social"
    text: str = Field(max_length=300)
    images: list[FileRef] = Field(default=[], max_length=4)

class SendEmail(BaseModel):
    tool: Literal["send_email"] = "send_email"
    to: str
    subject: str
    body: str
    images: list[FileRef] = Field(default=[], max_length=4)

PlannedAction = Annotated[PostSocial | SendEmail, Field(discriminator="tool")]

class ActionResult(BaseModel):
    action_id: str
    ok: bool
    url: str | None = None
    external_id: str | None = None
    error: str | None = None

class AuditEntry(BaseModel):
    action_id: str
    business_id: str
    team_id: str
    task_id: str
    tool: Literal["post_social", "send_email", "delete_social"]
    summary: str
    result: ActionResult
    autonomous: bool
    approval_id: str | None = None
    undo_until: datetime | None = None
    undone_at: datetime | None = None
    at: datetime
