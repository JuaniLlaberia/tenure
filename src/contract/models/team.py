from datetime import datetime

from pydantic import BaseModel

class Persona(BaseModel):
    name: str
    role: str

class Team(BaseModel):
    team_id: str
    business_id: str
    template: str
    display_name: str
    onboarded: bool = False
    created_at: datetime
