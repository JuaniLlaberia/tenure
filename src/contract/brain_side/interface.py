from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel

from contract.app_side.inputs import ApprovalDecision, IncomingMessage, PromotionResponse
from contract.brain_side.events import Event
from contract.models.team import Persona

class TemplateInfo(BaseModel):
    name: str
    display_name: str
    description: str
    personas: list[Persona]
    task_types: list[str]

class Brain(Protocol):
    def list_templates(self) -> list[TemplateInfo]: ...
    def start_onboarding(self, business_id: str) -> AsyncIterator[Event]: ...
    def handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]: ...
    def hire_team(self, business_id: str, template: str) -> AsyncIterator[Event]: ...
    def resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]: ...
    def respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]: ...
    def undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]: ...
