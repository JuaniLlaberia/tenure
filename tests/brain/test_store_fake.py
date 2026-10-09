from datetime import UTC, datetime, timedelta

import pytest

from brain.fakes import InMemoryStore
from contract import (
    ActionResult,
    Approval,
    AuditEntry,
    AutonomyLevel,
    BusinessProfile,
    Lesson,
    Task,
    TaskStatus,
    Team,
    Trust,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

def make_team(team_id="t1", business_id="b1"):
    return Team(
        team_id=team_id,
        business_id=business_id,
        template="marketing",
        display_name="Marketing",
        created_at=NOW,
    )

def make_task(task_id="k1"):
    return Task(
        task_id=task_id,
        business_id="b1",
        team_id="t1",
        task_type="social_post",
        title="Launch post",
        brief="Post about Friday",
        status=TaskStatus.PLANNED,
        steps=["writer"],
        created_at=NOW,
        updated_at=NOW,
    )

def make_approval(approval_id, status="pending", minutes=0, team_id="t1", task_type="social_post"):
    at = NOW + timedelta(minutes=minutes)
    return Approval(
        approval_id=approval_id,
        business_id="b1",
        team_id=team_id,
        task_id="k1",
        task_type=task_type,
        preview="We launch Friday",
        planned_action=None,
        check_confidence=0.9,
        status=status,
        created_at=at,
        resolved_at=None if status == "pending" else at,
    )

def make_lesson(lesson_id, team_id="t1", task_type=None, active=True, minutes=0, business_id="b1"):
    return Lesson(
        lesson_id=lesson_id,
        business_id=business_id,
        team_id=team_id,
        task_type=task_type,
        kind="preference",
        text="No emojis",
        source="chat",
        active=active,
        created_at=NOW + timedelta(minutes=minutes),
    )

def make_trust(task_type="social_post", streak=0, team_id="t1"):
    return Trust(
        team_id=team_id,
        task_type=task_type,
        level=AutonomyLevel.ACT_AFTER_APPROVAL,
        approval_streak=streak,
        updated_at=NOW,
    )

def make_entry(action_id="x1"):
    return AuditEntry(
        action_id=action_id,
        business_id="b1",
        team_id="t1",
        task_id="k1",
        tool="post_social",
        summary="Posted to Bluesky",
        result=ActionResult(action_id=action_id, ok=True),
        autonomous=False,
        at=NOW,
    )

def ids(records, field):
    return [getattr(r, field) for r in records]

@pytest.fixture
def store():
    return InMemoryStore()

async def test_get_returns_none_when_missing(store):
    assert await store.get_profile("b1") is None
    assert await store.get_team("t1") is None
    assert await store.get_trust("t1", "social_post") is None
    assert await store.get_task("k1") is None
    assert await store.get_approval("a1") is None
    assert await store.get_action("x1") is None
    assert await store.list_teams("b1") == []
    assert await store.list_lessons("b1", "t1") == []
    assert await store.recent_approvals("t1", "social_post") == []

async def test_save_is_an_upsert(store):
    await store.save_task(make_task())
    await store.save_task(make_task().model_copy(update={"status": TaskStatus.DONE}))
    assert (await store.get_task("k1")).status == TaskStatus.DONE

    await store.save_team(make_team())
    await store.save_team(make_team().model_copy(update={"onboarded": True}))
    teams = await store.list_teams("b1")
    assert len(teams) == 1
    assert teams[0].onboarded

    for name in ["A", "B"]:
        profile = BusinessProfile(business_id="b1", name=name, what_you_sell="x", customers="y")
        await store.save_profile(profile)
    assert (await store.get_profile("b1")).name == "B"

async def test_returned_records_are_copies(store):
    original = make_task()
    await store.save_task(original)
    original.title = "mutated after save"

    fetched = await store.get_task("k1")
    fetched.title = "mutated after get"

    assert (await store.get_task("k1")).title == "Launch post"

async def test_list_teams_filters_by_business(store):
    await store.save_team(make_team("t1", "b1"))
    await store.save_team(make_team("t2", "b1"))
    await store.save_team(make_team("t3", "b2"))
    assert set(ids(await store.list_teams("b1"), "team_id")) == {"t1", "t2"}

async def test_recent_approvals_only_resolved_newest_first(store):
    await store.save_approval(make_approval("a1", "approved", minutes=0))
    await store.save_approval(make_approval("a2", "edited", minutes=5))
    await store.save_approval(make_approval("a3", "pending", minutes=10))
    await store.save_approval(make_approval("a4", "rejected", minutes=3))
    await store.save_approval(make_approval("a5", "approved", minutes=8, team_id="t2"))
    await store.save_approval(make_approval("a6", "approved", minutes=9, task_type="newsletter"))

    recent = await store.recent_approvals("t1", "social_post")
    assert ids(recent, "approval_id") == ["a2", "a4", "a1"]

    limited = await store.recent_approvals("t1", "social_post", limit=2)
    assert ids(limited, "approval_id") == ["a2", "a4"]

async def test_list_lessons_business_wide_plus_team(store):
    await store.save_lesson(make_lesson("l1", team_id=None))
    await store.save_lesson(make_lesson("l2", team_id="t1"))
    await store.save_lesson(make_lesson("l3", team_id="t2"))
    await store.save_lesson(make_lesson("l4", team_id=None, business_id="b2"))

    assert set(ids(await store.list_lessons("b1", "t1"), "lesson_id")) == {"l1", "l2"}
    assert set(ids(await store.list_lessons("b1", None), "lesson_id")) == {"l1"}

async def test_list_lessons_filters_by_task_type(store):
    await store.save_lesson(make_lesson("l1", task_type=None))
    await store.save_lesson(make_lesson("l2", task_type="social_post"))
    await store.save_lesson(make_lesson("l3", task_type="newsletter"))

    lessons = await store.list_lessons("b1", "t1", "social_post")
    assert set(ids(lessons, "lesson_id")) == {"l1", "l2"}

async def test_list_lessons_only_active_newest_first(store):
    await store.save_lesson(make_lesson("l1", minutes=0))
    await store.save_lesson(make_lesson("l2", minutes=5))
    await store.save_lesson(make_lesson("l3", minutes=10, active=False))
    assert ids(await store.list_lessons("b1", "t1"), "lesson_id") == ["l2", "l1"]

    await store.save_lesson(make_lesson("l1", minutes=0, active=False))
    assert ids(await store.list_lessons("b1", "t1"), "lesson_id") == ["l2"]

async def test_trust_keyed_by_team_and_task_type(store):
    await store.set_trust(make_trust("social_post", streak=2))
    await store.set_trust(make_trust("newsletter", streak=0))
    await store.set_trust(make_trust("social_post", streak=3))

    assert (await store.get_trust("t1", "social_post")).approval_streak == 3
    assert (await store.get_trust("t1", "newsletter")).approval_streak == 0
    assert await store.get_trust("t2", "social_post") is None

async def test_audit_log_round_trip(store):
    entry = make_entry()
    await store.log_action(entry)
    assert await store.get_action("x1") == entry

    await store.log_action(entry.model_copy(update={"undone_at": NOW}))
    assert (await store.get_action("x1")).undone_at == NOW
