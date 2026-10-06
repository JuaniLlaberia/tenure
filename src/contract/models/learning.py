from datetime import datetime
from typing import Literal

from pydantic import BaseModel

class Lesson(BaseModel):
    lesson_id: str
    business_id: str
    team_id: str | None
    task_type: str | None = None
    kind: Literal["fact", "preference"]
    key: str | None = None
    text: str
    source: Literal["business_onboarding", "team_onboarding", "edit", "reject", "chat"]
    source_ref: str | None = None
    active: bool = True
    created_at: datetime
