from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel

from app.store.base import TelegramTopic
from contract import (
    Approval,
    AuditEntry,
    BusinessProfile,
    Lesson,
    Task,
    Team,
    Trust,
)

RESOLVED = ("approved", "edited", "rejected")

M = TypeVar("M", bound=BaseModel)

def _copy(model: M | None) -> M | None:
    return model.model_copy(deep=True) if model is not None else None

class InMemoryStore:
    """
    AppStore kept in dicts. Same behaviour as SupabaseStore; used when Supabase isn't configured.
    """

    def __init__(self) -> None:
        self._businesses: dict[int, str] = {}
        self._topics: dict[str, TelegramTopic] = {}
        self._profiles: dict[str, BusinessProfile] = {}
        self._teams: dict[str, Team] = {}
        self._trust: dict[tuple[str, str], Trust] = {}
        self._tasks: dict[str, Task] = {}
        self._approvals: dict[str, Approval] = {}
        self._lessons: dict[str, Lesson] = {}
        self._actions: dict[str, AuditEntry] = {}

    async def create_business(self, chat_id: int) -> str:
        business_id = str(uuid4())
        self._businesses[chat_id] = business_id
        return business_id

    async def list_businesses(self) -> dict[int, str]:
        return dict(self._businesses)

    async def save_topic(self, topic: TelegramTopic) -> None:
        self._topics[topic.team_id] = _copy(topic)

    async def list_topics(self) -> list[TelegramTopic]:
        return [_copy(topic) for topic in self._topics.values()]

    async def get_profile(self, business_id: str) -> BusinessProfile | None:
        return _copy(self._profiles.get(business_id))

    async def save_profile(self, profile: BusinessProfile) -> None:
        self._profiles[profile.business_id] = _copy(profile)

    async def save_team(self, team: Team) -> None:
        self._teams[team.team_id] = _copy(team)

    async def get_team(self, team_id: str) -> Team | None:
        return _copy(self._teams.get(team_id))

    async def list_teams(self, business_id: str) -> list[Team]:
        teams = [t for t in self._teams.values() if t.business_id == business_id]
        return [_copy(t) for t in sorted(teams, key=lambda t: t.created_at)]

    async def get_trust(self, team_id: str, task_type: str) -> Trust | None:
        return _copy(self._trust.get((team_id, task_type)))

    async def set_trust(self, trust: Trust) -> None:
        self._trust[(trust.team_id, trust.task_type)] = _copy(trust)

    async def save_task(self, task: Task) -> None:
        self._tasks[task.task_id] = _copy(task)

    async def get_task(self, task_id: str) -> Task | None:
        return _copy(self._tasks.get(task_id))

    async def save_approval(self, approval: Approval) -> None:
        self._approvals[approval.approval_id] = _copy(approval)

    async def get_approval(self, approval_id: str) -> Approval | None:
        return _copy(self._approvals.get(approval_id))

    async def recent_approvals(
        self, team_id: str, task_type: str, limit: int = 5
    ) -> list[Approval]:
        resolved = [
            a
            for a in self._approvals.values()
            if a.team_id == team_id and a.task_type == task_type and a.status in RESOLVED
        ]
        resolved.sort(key=lambda a: a.resolved_at or a.created_at, reverse=True)
        return [_copy(a) for a in resolved[:limit]]

    async def save_lesson(self, lesson: Lesson) -> None:
        self._lessons[lesson.lesson_id] = _copy(lesson)

    async def list_lessons(
        self, business_id: str, team_id: str | None, task_type: str | None = None
    ) -> list[Lesson]:
        lessons = [
            lesson
            for lesson in self._lessons.values()
            if lesson.business_id == business_id
            and lesson.active
            and (lesson.team_id is None or (team_id is not None and lesson.team_id == team_id))
            and (task_type is None or lesson.task_type in (None, task_type))
        ]
        lessons.sort(key=lambda lesson: lesson.created_at, reverse=True)
        return [_copy(lesson) for lesson in lessons]

    async def log_action(self, entry: AuditEntry) -> None:
        self._actions[entry.action_id] = _copy(entry)

    async def get_action(self, action_id: str) -> AuditEntry | None:
        return _copy(self._actions.get(action_id))
