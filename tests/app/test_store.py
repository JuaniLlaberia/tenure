"""
One suite for both stores. Supabase runs only with RUN_SUPABASE_TESTS=1 and the
schema applied; it writes to the real project and deletes its rows afterwards.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.store.base import TelegramTopic
from app.store.memory import InMemoryStore
from app.store.supabase_store import SupabaseStore
from contract import (
    ActionResult,
    Approval,
    AuditEntry,
    AutonomyLevel,
    BusinessProfile,
    Lesson,
    PostSocial,
    SendEmail,
    Task,
    TaskStatus,
    Team,
    Trust,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
LIVE = os.environ.get("RUN_SUPABASE_TESTS") == "1"

def new_id() -> str:
    return str(uuid4())

@pytest.fixture(params=["memory", "supabase"])
async def store(request):
    if request.param == "memory":
        yield InMemoryStore()
        return
    if not LIVE:
        pytest.skip("set RUN_SUPABASE_TESTS=1 to run against Supabase")
    store = SupabaseStore(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    store.created = []
    yield store
    db = await store._db()
    for business_id, team_ids in store.created:
        for team_id in team_ids:
            await db.table("trust").delete().eq("team_id", team_id).execute()
        await db.table("businesses").delete().eq("business_id", business_id).execute()

async def business(store, chat_id: int | None = None, teams: int = 1) -> tuple[str, list[str]]:
    business_id = await store.create_business(chat_id or -int(uuid4().int % 10**12))
    team_ids = [new_id() for _ in range(teams)]
    if isinstance(store, SupabaseStore):
        store.created.append((business_id, team_ids))
    return business_id, team_ids

def approval(business_id, team_id, status="pending", minutes=0, **fields) -> Approval:
    return Approval(
        approval_id=new_id(),
        business_id=business_id,
        team_id=team_id,
        task_id=new_id(),
        task_type="social_post",
        preview="Hello",
        planned_action=PostSocial(text="Hello"),
        check_confidence=0.9,
        status=status,
        created_at=NOW,
        resolved_at=NOW + timedelta(minutes=minutes) if status != "pending" else None,
        **fields,
    )

def lesson(business_id, team_id=None, task_type=None, minutes=0, **fields) -> Lesson:
    return Lesson(
        lesson_id=new_id(),
        business_id=business_id,
        team_id=team_id,
        task_type=task_type,
        kind="preference",
        text="No emojis",
        source="chat",
        created_at=NOW + timedelta(minutes=minutes),
        **fields,
    )

async def test_missing_records_return_none(store):
    assert await store.get_profile(new_id()) is None
    assert await store.get_team(new_id()) is None
    assert await store.get_trust(new_id(), "social_post") is None
    assert await store.get_task(new_id()) is None
    assert await store.get_approval(new_id()) is None
    assert await store.get_action(new_id()) is None

async def test_businesses_and_topics_round_trip(store):
    chat_id = -int(uuid4().int % 10**12)
    business_id, (team_id,) = await business(store, chat_id)
    assert (await store.list_businesses())[chat_id] == business_id
    topic = TelegramTopic(
        team_id=team_id, business_id=business_id, chat_id=chat_id, thread_id=7, name="Marketing"
    )
    await store.save_topic(topic)
    assert topic in await store.list_topics()

async def test_profile_upserts(store):
    business_id, _ = await business(store)
    profile = BusinessProfile(
        business_id=business_id,
        name="Bright Coaching",
        what_you_sell="Coaching",
        customers="Engineers",
        links=["https://bright.example"],
        extra={"city": "SF"},
    )
    await store.save_profile(profile)
    await store.save_profile(profile.model_copy(update={"tone": "warm"}))
    assert await store.get_profile(business_id) == profile.model_copy(update={"tone": "warm"})

async def test_teams_list_in_hire_order(store):
    business_id, (first, second) = await business(store, teams=2)
    late = Team(
        team_id=second, business_id=business_id, template="finance", display_name="Finance",
        created_at=NOW + timedelta(hours=1),
    )
    early = Team(
        team_id=first, business_id=business_id, template="marketing", display_name="Marketing",
        created_at=NOW,
    )
    await store.save_team(late)
    await store.save_team(early)
    await store.save_team(early.model_copy(update={"onboarded": True}))
    teams = await store.list_teams(business_id)
    assert [t.template for t in teams] == ["marketing", "finance"]
    assert teams[0].onboarded
    assert await store.get_team(first) == teams[0]

async def test_trust_upserts_per_task_type(store):
    _, (team_id,) = await business(store)
    trust = Trust(
        team_id=team_id,
        task_type="social_post",
        level=AutonomyLevel.ACT_AFTER_APPROVAL,
        updated_at=NOW,
    )
    await store.set_trust(trust)
    await store.set_trust(trust.model_copy(update={"approval_streak": 3, "promote_after": 2}))
    saved = await store.get_trust(team_id, "social_post")
    assert saved.approval_streak == 3
    assert saved.promote_after == 2
    assert saved.level is AutonomyLevel.ACT_AFTER_APPROVAL
    assert await store.get_trust(team_id, "newsletter") is None

async def test_task_round_trips(store):
    business_id, (team_id,) = await business(store)
    task = Task(
        task_id=new_id(),
        business_id=business_id,
        team_id=team_id,
        task_type="newsletter",
        title="Launch",
        brief="We launch Friday",
        status=TaskStatus.IN_PROGRESS,
        steps=["researcher", "writer"],
        created_at=NOW,
        updated_at=NOW,
    )
    await store.save_task(task)
    await store.save_task(task.model_copy(update={"status": TaskStatus.DONE, "current_step": 1}))
    saved = await store.get_task(task.task_id)
    assert saved.status is TaskStatus.DONE and saved.current_step == 1
    assert saved.steps == ["researcher", "writer"]

async def test_approval_keeps_its_planned_action(store):
    business_id, (team_id,) = await business(store)
    email = approval(business_id, team_id)
    email.planned_action = SendEmail(to="a@b.co", subject="Hi", body="Body")
    await store.save_approval(email)
    assert await store.get_approval(email.approval_id) == email

async def test_recent_approvals_are_resolved_newest_first(store):
    business_id, (team_id,) = await business(store)
    older = approval(business_id, team_id, "approved", minutes=1)
    newer = approval(business_id, team_id, "edited", minutes=2, edited_text="Hi")
    pending = approval(business_id, team_id)
    other_type = approval(business_id, team_id, "approved", minutes=3)
    other_type.task_type = "newsletter"
    for item in (older, newer, pending, other_type):
        await store.save_approval(item)
    recent = await store.recent_approvals(team_id, "social_post")
    assert [a.approval_id for a in recent] == [newer.approval_id, older.approval_id]
    assert len(await store.recent_approvals(team_id, "social_post", limit=1)) == 1

async def test_list_lessons_scopes(store):
    business_id, (team_id, other_team) = await business(store, teams=2)
    shared = lesson(business_id, minutes=1)
    mine = lesson(business_id, team_id, minutes=2)
    mine_posts = lesson(business_id, team_id, task_type="social_post", minutes=3)
    mine_emails = lesson(business_id, team_id, task_type="newsletter", minutes=4)
    theirs = lesson(business_id, other_team, minutes=5)
    forgotten = lesson(business_id, team_id, minutes=6, active=False)
    for item in (shared, mine, mine_posts, mine_emails, theirs, forgotten):
        await store.save_lesson(item)

    ids = lambda lessons: [item.lesson_id for item in lessons]  # noqa: E731
    assert ids(await store.list_lessons(business_id, None)) == [shared.lesson_id]
    assert ids(await store.list_lessons(business_id, team_id)) == [
        mine_emails.lesson_id,
        mine_posts.lesson_id,
        mine.lesson_id,
        shared.lesson_id,
    ]
    assert ids(await store.list_lessons(business_id, team_id, "social_post")) == [
        mine_posts.lesson_id,
        mine.lesson_id,
        shared.lesson_id,
    ]

async def test_forgetting_a_lesson_hides_it(store):
    business_id, (team_id,) = await business(store)
    item = lesson(business_id, team_id)
    await store.save_lesson(item)
    await store.save_lesson(item.model_copy(update={"active": False}))
    assert await store.list_lessons(business_id, team_id) == []

async def test_audit_log_round_trips_and_marks_undo(store):
    business_id, (team_id,) = await business(store)
    action_id = new_id()
    entry = AuditEntry(
        action_id=action_id,
        business_id=business_id,
        team_id=team_id,
        task_id=new_id(),
        tool="post_social",
        summary="Posted to Bluesky",
        result=ActionResult(action_id=action_id, ok=True, external_id="at://did/post/1"),
        autonomous=False,
        approval_id=new_id(),
        undo_until=NOW + timedelta(minutes=10),
        at=NOW,
    )
    await store.log_action(entry)
    await store.log_action(entry.model_copy(update={"undone_at": NOW + timedelta(minutes=2)}))
    saved = await store.get_action(action_id)
    assert saved.undone_at == NOW + timedelta(minutes=2)
    assert saved.result.external_id == "at://did/post/1"

async def test_returned_models_are_copies(store):
    business_id, (team_id,) = await business(store)
    item = lesson(business_id, team_id)
    await store.save_lesson(item)
    (loaded,) = await store.list_lessons(business_id, team_id)
    loaded.active = False
    assert await store.list_lessons(business_id, team_id) != []

async def test_dashboard_access_set_and_cleared(store):
    business_id, _ = await business(store)
    assert await store.dashboard_token(business_id) is None
    token = f"tok-{new_id()}"
    await store.set_dashboard(business_id, token, "scrypt$aa$bb")
    assert await store.dashboard_token(business_id) == token
    assert await store.dashboard_access(token) == (business_id, "scrypt$aa$bb")
    assert await store.dashboard_access("not-a-token") is None
    await store.set_dashboard(business_id, None, None)
    assert await store.dashboard_token(business_id) is None
    assert await store.dashboard_access(token) is None

async def test_list_trust_for_a_team(store):
    _, (team_id,) = await business(store)
    for task_type in ("social_post", "newsletter"):
        await store.set_trust(
            Trust(team_id=team_id, task_type=task_type, level=AutonomyLevel.ACT_AFTER_APPROVAL,
                  updated_at=NOW)
        )
    assert [t.task_type for t in await store.list_trust(team_id)] == ["newsletter", "social_post"]

async def test_dashboard_lists_are_newest_first(store):
    business_id, (team_id,) = await business(store)
    other_business, _ = await business(store)
    for minutes in (1, 3, 2):
        await store.save_task(
            Task(task_id=new_id(), business_id=business_id, team_id=team_id,
                 task_type="social_post", title=f"t{minutes}", brief="b",
                 status=TaskStatus.DONE, steps=["writer"], created_at=NOW,
                 updated_at=NOW + timedelta(minutes=minutes))
        )
    assert [t.title for t in await store.list_tasks(business_id)] == ["t3", "t2", "t1"]
    assert await store.list_tasks(other_business) == []

    waiting = approval(business_id, team_id)
    done = approval(business_id, team_id, "approved", minutes=1)
    await store.save_approval(waiting)
    await store.save_approval(done)
    pending = await store.list_approvals(business_id, "pending")
    assert [a.approval_id for a in pending] == [waiting.approval_id]
    assert len(await store.list_approvals(business_id)) == 2

    for minutes in (1, 2):
        action_id = new_id()
        await store.log_action(
            AuditEntry(action_id=action_id, business_id=business_id, team_id=team_id,
                       task_id=new_id(), tool="send_email", summary=f"a{minutes}",
                       result=ActionResult(action_id=action_id, ok=True), autonomous=False,
                       at=NOW + timedelta(minutes=minutes))
        )
    assert [a.summary for a in await store.list_actions(business_id)] == ["a2", "a1"]

    shared = lesson(business_id, minutes=1)
    mine = lesson(business_id, team_id, minutes=2)
    hidden = lesson(business_id, team_id, minutes=3, active=False)
    for item in (shared, mine, hidden):
        await store.save_lesson(item)
    assert [x.lesson_id for x in await store.list_all_lessons(business_id)] == [
        mine.lesson_id,
        shared.lesson_id,
    ]

async def test_delete_team_removes_its_records_but_keeps_the_audit_log(store):
    business_id, (team_id, other_team) = await business(store, teams=2)
    for current in (team_id, other_team):
        await store.save_team(
            Team(team_id=current, business_id=business_id, template="marketing",
                 display_name="Marketing", created_at=NOW)
        )
        await store.set_trust(
            Trust(team_id=current, task_type="social_post",
                  level=AutonomyLevel.ACT_AFTER_APPROVAL, updated_at=NOW)
        )
    task = Task(task_id=new_id(), business_id=business_id, team_id=team_id,
                task_type="social_post", title="t", brief="b", status=TaskStatus.DONE,
                steps=["writer"], created_at=NOW, updated_at=NOW)
    await store.save_task(task)
    draft = approval(business_id, team_id)
    await store.save_approval(draft)
    team_lesson = lesson(business_id, team_id)
    shared = lesson(business_id)
    await store.save_lesson(team_lesson)
    await store.save_lesson(shared)
    action_id = new_id()
    await store.log_action(
        AuditEntry(action_id=action_id, business_id=business_id, team_id=team_id,
                   task_id=task.task_id, tool="send_email", summary="Sent",
                   result=ActionResult(action_id=action_id, ok=True), autonomous=False, at=NOW)
    )

    await store.delete_team(team_id)
    assert await store.get_team(team_id) is None
    assert await store.get_trust(team_id, "social_post") is None
    assert await store.get_task(task.task_id) is None
    assert await store.get_approval(draft.approval_id) is None
    assert [x.lesson_id for x in await store.list_all_lessons(business_id)] == [shared.lesson_id]
    assert await store.get_action(action_id) is not None
    assert await store.get_team(other_team) is not None

async def test_state_items_round_trip_and_prune(store):
    kind = f"test-{new_id()}"
    await store.save_state(kind, '["1", 2]', {"a": [1, 2], "b": None})
    await store.save_state(kind, "k2", {})
    await store.save_state(kind, "k2", {"x": 1})
    mine = sorted((k, d) for kd, k, d in await store.list_state() if kd == kind)
    assert mine == [('["1", 2]', {"a": [1, 2], "b": None}), ("k2", {"x": 1})]
    await store.delete_state(kind, "k2")
    await store.prune_state(datetime.now(UTC) - timedelta(days=1))
    assert [k for kd, k, _ in await store.list_state() if kd == kind] == ['["1", 2]']
    await store.prune_state(datetime.now(UTC) + timedelta(seconds=5))
    assert [k for kd, k, _ in await store.list_state() if kd == kind] == []
