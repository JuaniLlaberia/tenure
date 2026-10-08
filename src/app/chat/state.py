"""
What the Telegram side remembers: group and topic mappings, and the messages
it may edit later. In memory for now; the mappings move to Supabase with the Store.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import uuid4

from app.chat.port import Keyboard
from contract import AutonomyLevel, Persona, PlannedAction

Where = tuple[int, int | None]

@dataclass
class Card:
    chat_id: int
    thread_id: int | None
    message_id: int
    text: str
    keyboard: Keyboard

@dataclass
class ApprovalCard(Card):
    business_id: str
    team_id: str
    approval_id: str
    persona: Persona
    action: PlannedAction | None

@dataclass
class ActionCard(Card):
    business_id: str
    team_id: str
    summary: str
    undo_until: datetime | None

@dataclass
class AskCard(Card):
    business_id: str
    team_id: str | None
    replies: list[str]

@dataclass
class OfferCard(Card):
    business_id: str
    team_id: str
    task_type: str
    persona: Persona
    proposed: AutonomyLevel

@dataclass
class LessonCard(Card):
    business_id: str
    team_id: str | None
    persona: Persona

@dataclass
class Pending:
    kind: Literal["edit", "reason"]
    approval_id: str

@dataclass
class AppState:
    businesses: dict[int, str] = field(default_factory=dict)
    topics: dict[tuple[int, int], str] = field(default_factory=dict)
    team_threads: dict[str, int] = field(default_factory=dict)
    team_names: dict[str, str] = field(default_factory=dict)
    approvals: dict[str, ApprovalCard] = field(default_factory=dict)
    actions: dict[str, ActionCard] = field(default_factory=dict)
    asks: dict[str, AskCard] = field(default_factory=dict)
    offers: dict[str, OfferCard] = field(default_factory=dict)
    lessons: dict[str, LessonCard] = field(default_factory=dict)
    hire_cards: dict[tuple[int, int], Card] = field(default_factory=dict)
    pending: dict[Where, Pending] = field(default_factory=dict)
    status: dict[Where, int] = field(default_factory=dict)
    handled: set[tuple[int, int]] = field(default_factory=set)

    def add_business(self, chat_id: int) -> str:
        business_id = str(uuid4())
        self.businesses[chat_id] = business_id
        return business_id

    def add_team(self, chat_id: int, thread_id: int, team_id: str, name: str) -> None:
        self.topics[(chat_id, thread_id)] = team_id
        self.team_threads[team_id] = thread_id
        self.team_names[team_id] = name
