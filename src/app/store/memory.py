from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from app.store.base import TelegramTopic
from contract import (
    Approval,
    AuditEntry,
    BusinessProfile,
    FileRef,
    Lesson,
    ModelUsage,
    Schedule,
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
        self._usage: list[ModelUsage] = []
        self._files: dict[str, tuple[FileRef, bytes]] = {}
        self._schedules: dict[str, Schedule] = {}
        self._dashboards: dict[str, tuple[str | None, str | None]] = {}
        self._state: dict[tuple[str, str], tuple[Any, datetime]] = {}

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

    async def dashboard_token(self, business_id: str) -> str | None:
        return self._dashboards.get(business_id, (None, None))[0]

    async def set_dashboard(
        self, business_id: str, token: str | None, password_hash: str | None
    ) -> None:
        self._dashboards[business_id] = (token, password_hash)

    async def dashboard_access(self, token: str) -> tuple[str, str] | None:
        for business_id, (known, password_hash) in self._dashboards.items():
            if known == token and password_hash:
                return business_id, password_hash
        return None

    async def list_trust(self, team_id: str) -> list[Trust]:
        rows = [t for (team, _), t in self._trust.items() if team == team_id]
        return [_copy(t) for t in sorted(rows, key=lambda t: t.task_type)]

    async def list_tasks(self, business_id: str, limit: int = 50) -> list[Task]:
        tasks = [t for t in self._tasks.values() if t.business_id == business_id]
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return [_copy(t) for t in tasks[:limit]]

    async def list_approvals(
        self, business_id: str, status: str | None = None, limit: int = 100
    ) -> list[Approval]:
        approvals = [
            a
            for a in self._approvals.values()
            if a.business_id == business_id and (status is None or a.status == status)
        ]
        approvals.sort(key=lambda a: a.created_at, reverse=True)
        return [_copy(a) for a in approvals[:limit]]

    async def list_actions(self, business_id: str, limit: int = 50) -> list[AuditEntry]:
        actions = [a for a in self._actions.values() if a.business_id == business_id]
        actions.sort(key=lambda a: a.at, reverse=True)
        return [_copy(a) for a in actions[:limit]]

    async def list_usage(self, business_id: str, since: datetime) -> list[ModelUsage]:
        return [
            _copy(u) for u in self._usage if u.business_id == business_id and u.at >= since
        ]

    async def list_all_lessons(self, business_id: str) -> list[Lesson]:
        lessons = [
            lesson
            for lesson in self._lessons.values()
            if lesson.business_id == business_id and lesson.active
        ]
        lessons.sort(key=lambda lesson: lesson.created_at, reverse=True)
        return [_copy(lesson) for lesson in lessons]

    async def save_state(self, kind: str, key: str, data: Any) -> None:
        self._state[(kind, key)] = (data, datetime.now(UTC))

    async def delete_state(self, kind: str, key: str) -> None:
        self._state.pop((kind, key), None)

    async def list_state(self) -> list[tuple[str, str, Any]]:
        return [(kind, key, data) for (kind, key), (data, _) in self._state.items()]

    async def prune_state(self, before: datetime) -> None:
        for item in [k for k, (_, at) in self._state.items() if at < before]:
            del self._state[item]

    async def delete_team(self, team_id: str) -> None:
        self._teams.pop(team_id, None)
        self._topics.pop(team_id, None)
        for key in [k for k in self._trust if k[0] == team_id]:
            del self._trust[key]
        for records in (self._tasks, self._approvals, self._lessons, self._schedules):
            for key in [k for k, v in records.items() if v.team_id == team_id]:
                del records[key]

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

    async def log_usage(self, usage: ModelUsage) -> None:
        self._usage.append(_copy(usage))

    async def save_schedule(self, schedule: Schedule) -> None:
        self._schedules[schedule.schedule_id] = _copy(schedule)

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        return _copy(self._schedules.get(schedule_id))

    async def list_schedules(
        self, business_id: str, team_id: str | None = None
    ) -> list[Schedule]:
        found = [
            s
            for s in self._schedules.values()
            if s.business_id == business_id and team_id in (None, s.team_id)
        ]
        return [_copy(s) for s in sorted(found, key=lambda s: s.created_at, reverse=True)]

    async def due_schedules(self, now: datetime) -> list[Schedule]:
        return [
            _copy(s)
            for s in self._schedules.values()
            if s.active and (s.next_run_at is None or s.next_run_at <= now)
        ]

    async def store_file(self, ref: FileRef, data: bytes) -> None:
        self._files[ref.file_id] = (_copy(ref), bytes(data))

    async def get_file(self, business_id: str, file_id: str) -> FileRef | None:
        found = self._files.get(file_id)
        if found is None or found[0].business_id != business_id:
            return None
        return _copy(found[0])

    async def file_bytes(self, business_id: str, file_id: str) -> bytes | None:
        found = self._files.get(file_id)
        if found is None or found[0].business_id != business_id:
            return None
        return found[1]
