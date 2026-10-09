from datetime import datetime

from pydantic import BaseModel

class ModelUsage(BaseModel):
    usage_id: str
    business_id: str
    team_id: str | None = None
    task_id: str | None = None
    model: str
    purpose: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    at: datetime
