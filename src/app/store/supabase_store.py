from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel
from supabase import AsyncClient, acreate_client

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

RESOLVED = ["approved", "edited", "rejected"]

M = TypeVar("M", bound=BaseModel)

def _row(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")

def _topic_row(topic: TelegramTopic) -> dict[str, Any]:
    row = _row(topic)
    row["telegram_chat_id"] = row.pop("chat_id")
    return row

class SupabaseStore:
    """
    AppStore on Supabase (PostgREST). Tables come from schema.sql; one column per model field.
    """

    def __init__(self, url: str, key: str) -> None:
        self._url = url
        self._key = key
        self._client: AsyncClient | None = None

    async def _db(self) -> AsyncClient:
        if self._client is None:
            self._client = await acreate_client(self._url, self._key)
        return self._client

    async def _upsert(self, table: str, row: dict[str, Any], on_conflict: str = "") -> None:
        db = await self._db()
        await db.table(table).upsert(row, on_conflict=on_conflict).execute()

    async def _get(self, table: str, model: type[M], **match: str) -> M | None:
        db = await self._db()
        query = db.table(table).select("*")
        for column, value in match.items():
            query = query.eq(column, value)
        rows = (await query.limit(1).execute()).data
        return model.model_validate(rows[0]) if rows else None

    async def create_business(self, chat_id: int) -> str:
        business_id = str(uuid4())
        await self._upsert(
            "businesses", {"business_id": business_id, "telegram_chat_id": chat_id}
        )
        return business_id

    async def list_businesses(self) -> dict[int, str]:
        db = await self._db()
        rows = (await db.table("businesses").select("business_id, telegram_chat_id").execute()).data
        return {row["telegram_chat_id"]: row["business_id"] for row in rows}

    async def save_topic(self, topic: TelegramTopic) -> None:
        await self._upsert("telegram_topics", _topic_row(topic))

    async def list_topics(self) -> list[TelegramTopic]:
        db = await self._db()
        rows = (await db.table("telegram_topics").select("*").execute()).data
        return [
            TelegramTopic.model_validate({**row, "chat_id": row["telegram_chat_id"]})
            for row in rows
        ]

    async def get_profile(self, business_id: str) -> BusinessProfile | None:
        return await self._get("business_profiles", BusinessProfile, business_id=business_id)

    async def save_profile(self, profile: BusinessProfile) -> None:
        await self._upsert("business_profiles", _row(profile))

    async def save_team(self, team: Team) -> None:
        await self._upsert("teams", _row(team))

    async def get_team(self, team_id: str) -> Team | None:
        return await self._get("teams", Team, team_id=team_id)

    async def list_teams(self, business_id: str) -> list[Team]:
        db = await self._db()
        query = db.table("teams").select("*").eq("business_id", business_id).order("created_at")
        return [Team.model_validate(row) for row in (await query.execute()).data]

    async def get_trust(self, team_id: str, task_type: str) -> Trust | None:
        return await self._get("trust", Trust, team_id=team_id, task_type=task_type)

    async def set_trust(self, trust: Trust) -> None:
        await self._upsert("trust", _row(trust), on_conflict="team_id,task_type")

    async def save_task(self, task: Task) -> None:
        await self._upsert("tasks", _row(task))

    async def get_task(self, task_id: str) -> Task | None:
        return await self._get("tasks", Task, task_id=task_id)

    async def save_approval(self, approval: Approval) -> None:
        await self._upsert("approvals", _row(approval))

    async def get_approval(self, approval_id: str) -> Approval | None:
        return await self._get("approvals", Approval, approval_id=approval_id)

    async def recent_approvals(
        self, team_id: str, task_type: str, limit: int = 5
    ) -> list[Approval]:
        db = await self._db()
        query = (
            db.table("approvals")
            .select("*")
            .eq("team_id", team_id)
            .eq("task_type", task_type)
            .in_("status", RESOLVED)
            .order("resolved_at", desc=True)
            .limit(limit)
        )
        return [Approval.model_validate(row) for row in (await query.execute()).data]

    async def save_lesson(self, lesson: Lesson) -> None:
        await self._upsert("lessons", _row(lesson))

    async def list_lessons(
        self, business_id: str, team_id: str | None, task_type: str | None = None
    ) -> list[Lesson]:
        db = await self._db()
        query = db.table("lessons").select("*").eq("business_id", business_id).eq("active", True)
        if team_id is None:
            query = query.is_("team_id", "null")
        else:
            query = query.or_(f"team_id.is.null,team_id.eq.{team_id}")
        rows = (await query.order("created_at", desc=True).execute()).data
        lessons = [Lesson.model_validate(row) for row in rows]
        if task_type is None:
            return lessons
        return [lesson for lesson in lessons if lesson.task_type in (None, task_type)]

    async def log_action(self, entry: AuditEntry) -> None:
        await self._upsert("audit_log", _row(entry))

    async def get_action(self, action_id: str) -> AuditEntry | None:
        return await self._get("audit_log", AuditEntry, action_id=action_id)
