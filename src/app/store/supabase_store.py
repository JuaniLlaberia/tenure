from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel
from supabase import AsyncClient, acreate_client

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

RESOLVED = ["approved", "edited", "rejected"]
BUCKET = "files"

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

    async def dashboard_token(self, business_id: str) -> str | None:
        db = await self._db()
        query = db.table("businesses").select("dashboard_token").eq("business_id", business_id)
        rows = (await query.execute()).data
        return rows[0]["dashboard_token"] if rows else None

    async def set_dashboard(
        self, business_id: str, token: str | None, password_hash: str | None
    ) -> None:
        db = await self._db()
        update = {"dashboard_token": token, "dashboard_password_hash": password_hash}
        await db.table("businesses").update(update).eq("business_id", business_id).execute()

    async def dashboard_access(self, token: str) -> tuple[str, str] | None:
        db = await self._db()
        query = (
            db.table("businesses")
            .select("business_id, dashboard_password_hash")
            .eq("dashboard_token", token)
            .limit(1)
        )
        rows = (await query.execute()).data
        if not rows or not rows[0]["dashboard_password_hash"]:
            return None
        return rows[0]["business_id"], rows[0]["dashboard_password_hash"]

    async def list_trust(self, team_id: str) -> list[Trust]:
        db = await self._db()
        query = db.table("trust").select("*").eq("team_id", team_id).order("task_type")
        return [Trust.model_validate(row) for row in (await query.execute()).data]

    async def list_tasks(self, business_id: str, limit: int = 50) -> list[Task]:
        db = await self._db()
        query = (
            db.table("tasks")
            .select("*")
            .eq("business_id", business_id)
            .order("updated_at", desc=True)
            .limit(limit)
        )
        return [Task.model_validate(row) for row in (await query.execute()).data]

    async def list_approvals(
        self, business_id: str, status: str | None = None, limit: int = 100
    ) -> list[Approval]:
        db = await self._db()
        query = db.table("approvals").select("*").eq("business_id", business_id)
        if status is not None:
            query = query.eq("status", status)
        query = query.order("created_at", desc=True).limit(limit)
        return [Approval.model_validate(row) for row in (await query.execute()).data]

    async def list_actions(self, business_id: str, limit: int = 50) -> list[AuditEntry]:
        db = await self._db()
        query = (
            db.table("audit_log")
            .select("*")
            .eq("business_id", business_id)
            .order("at", desc=True)
            .limit(limit)
        )
        return [AuditEntry.model_validate(row) for row in (await query.execute()).data]

    async def list_usage(self, business_id: str, since: datetime) -> list[ModelUsage]:
        db = await self._db()
        query = (
            db.table("model_usage")
            .select("*")
            .eq("business_id", business_id)
            .gte("at", since.isoformat())
            .order("at", desc=True)
        )
        return [ModelUsage.model_validate(row) for row in (await query.execute()).data]

    async def list_all_lessons(self, business_id: str) -> list[Lesson]:
        db = await self._db()
        query = (
            db.table("lessons")
            .select("*")
            .eq("business_id", business_id)
            .eq("active", True)
            .order("created_at", desc=True)
        )
        return [Lesson.model_validate(row) for row in (await query.execute()).data]

    async def save_state(self, kind: str, key: str, data: Any) -> None:
        row = {"kind": kind, "key": key, "data": data, "updated_at": datetime.now(UTC).isoformat()}
        await self._upsert("telegram_state", row, on_conflict="kind,key")

    async def delete_state(self, kind: str, key: str) -> None:
        db = await self._db()
        await db.table("telegram_state").delete().eq("kind", kind).eq("key", key).execute()

    async def list_state(self) -> list[tuple[str, str, Any]]:
        db = await self._db()
        rows = (await db.table("telegram_state").select("kind, key, data").execute()).data
        return [(row["kind"], row["key"], row["data"]) for row in rows]

    async def prune_state(self, before: datetime) -> None:
        db = await self._db()
        await db.table("telegram_state").delete().lt("updated_at", before.isoformat()).execute()

    async def delete_team(self, team_id: str) -> None:
        db = await self._db()
        tables = ("telegram_topics", "trust", "approvals", "tasks", "lessons", "schedules", "teams")
        for table in tables:
            await db.table(table).delete().eq("team_id", team_id).execute()

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

    async def log_usage(self, usage: ModelUsage) -> None:
        db = await self._db()
        await db.table("model_usage").insert(_row(usage)).execute()

    async def save_schedule(self, schedule: Schedule) -> None:
        await self._upsert("schedules", _row(schedule))

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        return await self._get("schedules", Schedule, schedule_id=schedule_id)

    async def list_schedules(
        self, business_id: str, team_id: str | None = None
    ) -> list[Schedule]:
        db = await self._db()
        query = db.table("schedules").select("*").eq("business_id", business_id)
        if team_id is not None:
            query = query.eq("team_id", team_id)
        rows = (await query.order("created_at", desc=True).execute()).data
        return [Schedule.model_validate(row) for row in rows]

    async def due_schedules(self, now: datetime) -> list[Schedule]:
        db = await self._db()
        query = (
            db.table("schedules")
            .select("*")
            .eq("active", True)
            .or_(f"next_run_at.is.null,next_run_at.lte.{now.isoformat()}")
        )
        return [Schedule.model_validate(row) for row in (await query.execute()).data]

    async def store_file(self, ref: FileRef, data: bytes) -> None:
        db = await self._db()
        await db.storage.from_(BUCKET).upload(
            f"{ref.business_id}/{ref.file_id}", data, {"content-type": ref.mime_type}
        )
        await self._upsert("files", _row(ref))

    async def get_file(self, business_id: str, file_id: str) -> FileRef | None:
        return await self._get("files", FileRef, business_id=business_id, file_id=file_id)

    async def file_bytes(self, business_id: str, file_id: str) -> bytes | None:
        db = await self._db()
        try:
            return await db.storage.from_(BUCKET).download(f"{business_id}/{file_id}")
        except Exception:
            return None
