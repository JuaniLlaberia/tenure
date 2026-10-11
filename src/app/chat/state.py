"""
What the Telegram side remembers: group and topic mappings, the messages it may edit
later, pending edit/reason prompts and password messages to delete. All of it survives a
restart: mappings live in their own tables, the rest in the store's state table.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from app.chat.port import Keyboard
from contract import ApprovalDecision, AutonomyLevel, Persona, PlannedAction, Schedule

if TYPE_CHECKING:
    from app.store.base import AppStore

logger = logging.getLogger(__name__)

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
    task_id: str | None = None
    editable: bool = True
    new_image: bool = False
    retry: ApprovalDecision | None = None

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
class ReplaceCard(Card):
    business_id: str
    template: str
    name: str

@dataclass
class ScheduleCard(Card):
    business_id: str
    team_id: str
    persona: Persona
    schedule: Schedule

@dataclass
class Pending:
    kind: Literal["edit", "reason", "image", "text", "subject", "to", "time"]
    approval_id: str
    since: datetime | None = None

@dataclass
class Timed:
    """
    A draft with a send time. Once the founder approves it, `decision` waits here until then.
    """

    business_id: str
    team_id: str
    task_id: str | None
    send_at: datetime
    timezone: str
    decision: ApprovalDecision | None = None

@dataclass
class EditDraft:
    """
    The founder's version of a draft, built in Telegram one change at a time before approving.
    """

    approval_id: str
    action: PlannedAction
    original: PlannedAction
    changes: list[str]
    message_id: int | None = None

@dataclass
class PasswordMessage:
    message_id: int
    delete_at: datetime

class Journal:
    """
    Writes state changes to the store in the background, one at a time and in order, so
    handlers never wait on the database. Failures are logged; the in-memory state stays right.
    """

    def __init__(self) -> None:
        self.store: AppStore | None = None
        self._queue: asyncio.Queue[tuple[str, str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def record(self, kind: str, key: str, data: Any) -> None:
        if self.store is None:
            return
        self._queue.put_nowait((kind, key, data))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        while not self._queue.empty():
            kind, key, data = self._queue.get_nowait()
            try:
                if data is None:
                    await self.store.delete_state(kind, key)
                else:
                    await self.store.save_state(kind, key, data)
            except Exception:
                logger.exception("Saving %s %s failed", kind, key)
            finally:
                self._queue.task_done()

    async def flush(self) -> None:
        await self._queue.join()

def _encode_key(key: Any) -> str:
    return json.dumps(list(key)) if isinstance(key, tuple) else str(key)

class Saved(dict):
    """
    A dict whose changes are journaled under one kind. load() fills it without journaling.
    """

    def __init__(self, journal: Journal, kind: str, model: Any, key: Callable[[str], Any]) -> None:
        super().__init__()
        self._journal = journal
        self.kind = kind
        self._adapter = TypeAdapter(model)
        self._key = key

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        data = self._adapter.dump_python(value, mode="json")
        self._journal.record(self.kind, _encode_key(key), data)

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        self._journal.record(self.kind, _encode_key(key), None)

    def pop(self, key: Any, *default: Any) -> Any:
        present = key in self
        value = super().pop(key, *default)
        if present:
            self._journal.record(self.kind, _encode_key(key), None)
        return value

    def load(self, key: str, data: Any) -> None:
        super().__setitem__(self._key(key), self._adapter.validate_python(data))

    def clear_all(self) -> None:
        for key in list(self):
            del self[key]

class SavedSet(set):
    def __init__(self, journal: Journal, kind: str) -> None:
        super().__init__()
        self._journal = journal
        self.kind = kind

    def add(self, item: tuple[int, int]) -> None:
        super().add(item)
        self._journal.record(self.kind, _encode_key(item), {})

    def discard(self, item: tuple[int, int]) -> None:
        present = item in self
        super().discard(item)
        if present:
            self._journal.record(self.kind, _encode_key(item), None)

    def load(self, key: str, data: Any) -> None:
        super().add(_tuple_key(key))

def _tuple_key(key: str) -> tuple:
    return tuple(json.loads(key))

class AppState:
    """
    Group and topic mappings come from the store's own tables; everything else here is
    journaled to the store's state table so buttons keep working after a restart.
    """

    def __init__(self) -> None:
        self.journal = Journal()
        j = self.journal
        self.businesses: dict[int, str] = {}
        self.topics: dict[tuple[int, int], str] = {}
        self.team_threads: dict[str, int] = {}
        self.team_names: dict[str, str] = {}
        self.approvals = Saved(j, "approval", ApprovalCard, str)
        self.actions = Saved(j, "action", ActionCard, str)
        self.asks = Saved(j, "ask", AskCard, str)
        self.offers = Saved(j, "offer", OfferCard, str)
        self.lessons = Saved(j, "lesson", LessonCard, str)
        self.hire_cards = Saved(j, "hire", Card, _tuple_key)
        self.replacements = Saved(j, "replace", ReplaceCard, str)
        self.pending = Saved(j, "pending", Pending, _tuple_key)
        self.status = Saved(j, "status", int, _tuple_key)
        self.passwords = Saved(j, "password", PasswordMessage, int)
        self.schedules = Saved(j, "schedule", ScheduleCard, str)
        self.edits = Saved(j, "editdraft", EditDraft, str)
        self.timed = Saved(j, "timed", Timed, str)
        self.handled = SavedSet(j, "handled")

    def saved(self) -> dict[str, Saved | SavedSet]:
        containers = [
            self.approvals, self.actions, self.asks, self.offers, self.lessons, self.hire_cards,
            self.replacements, self.pending, self.status, self.passwords, self.handled,
            self.schedules, self.edits, self.timed,
        ]
        return {container.kind: container for container in containers}

    def restore(self, items: list[tuple[str, str, Any]]) -> None:
        containers = self.saved()
        for kind, key, data in items:
            container = containers.get(kind)
            if container is None:
                continue
            try:
                container.load(key, data)
            except Exception:
                logger.warning("Skipping saved %s %s that no longer fits", kind, key)

    def add_team(self, chat_id: int, thread_id: int, team_id: str, name: str) -> None:
        self.topics[(chat_id, thread_id)] = team_id
        self.team_threads[team_id] = thread_id
        self.team_names[team_id] = name
