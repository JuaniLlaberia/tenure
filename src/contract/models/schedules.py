from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

class Cadence(BaseModel):
    every: Literal["day", "week", "month"]
    weekday: int | None = Field(default=None, ge=0, le=6)
    day: int | None = Field(default=None, ge=1, le=28)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    timezone: str = "America/Los_Angeles"

class Schedule(BaseModel):
    schedule_id: str
    business_id: str
    team_id: str
    title: str
    request: str
    cadence: Cadence
    active: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime
