from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from contract.models.actions import PlannedAction
from contract.models.autonomy import AutonomyLevel
from contract.models.files import FileRef
from contract.models.schedules import Schedule
from contract.models.team import Persona

class Say(BaseModel):
    type: Literal["say"] = "say"
    team_id: str | None
    task_id: str | None = None
    persona: Persona
    text: str
    media: list[FileRef] = []

class Progress(BaseModel):
    type: Literal["progress"] = "progress"
    team_id: str | None
    task_id: str | None = None
    persona: Persona
    status: str

class Ask(BaseModel):
    type: Literal["ask"] = "ask"
    team_id: str | None
    persona: Persona
    question: str
    quick_replies: list[str] = []

class NeedsApproval(BaseModel):
    type: Literal["needs_approval"] = "needs_approval"
    approval_id: str
    team_id: str
    task_id: str
    task_type: str
    persona: Persona
    preview: str
    planned_action: PlannedAction | None
    media: list[FileRef] = []
    check_confidence: float

class ActionDone(BaseModel):
    type: Literal["action_done"] = "action_done"
    action_id: str
    team_id: str
    task_id: str
    summary: str
    url: str | None
    autonomous: bool
    undo_until: datetime | None

class ActionUndone(BaseModel):
    type: Literal["action_undone"] = "action_undone"
    action_id: str
    team_id: str
    summary: str

class PromotionOffer(BaseModel):
    type: Literal["promotion_offer"] = "promotion_offer"
    team_id: str
    task_type: str
    persona: Persona
    current_level: AutonomyLevel
    proposed_level: AutonomyLevel
    evidence: str

class LessonLearned(BaseModel):
    type: Literal["lesson_learned"] = "lesson_learned"
    lesson_id: str
    team_id: str | None
    persona: Persona
    text: str
    business_wide: bool

class TeamHired(BaseModel):
    type: Literal["team_hired"] = "team_hired"
    team_id: str
    template: str
    display_name: str
    personas: list[Persona]

class ScheduleSaved(BaseModel):
    type: Literal["schedule_saved"] = "schedule_saved"
    team_id: str
    persona: Persona
    schedule: Schedule

class OnboardingComplete(BaseModel):
    type: Literal["onboarding_complete"] = "onboarding_complete"
    scope: Literal["business", "team"]
    team_id: str | None

class Error(BaseModel):
    type: Literal["error"] = "error"
    team_id: str | None
    message: str
    recoverable: bool

Event = Annotated[
    Say
    | Progress
    | Ask
    | NeedsApproval
    | ActionDone
    | ActionUndone
    | PromotionOffer
    | LessonLearned
    | TeamHired
    | ScheduleSaved
    | OnboardingComplete
    | Error,
    Field(discriminator="type"),
]
