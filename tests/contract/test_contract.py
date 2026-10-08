from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from contract import (
    ActionDone,
    ActionResult,
    ActionUndone,
    Approval,
    Ask,
    AuditEntry,
    AutonomyLevel,
    Error,
    Event,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    Persona,
    PlannedAction,
    PostSocial,
    Progress,
    PromotionOffer,
    Say,
    SendEmail,
    TeamHired,
    Trust,
)

NOW = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
MAYA = Persona(name="Maya", role="Marketing lead")

EVENTS = [
    Say(team_id="t1", persona=MAYA, text="On it."),
    Progress(team_id="t1", task_id="k1", persona=MAYA, status="Drafting the post"),
    Ask(team_id=None, persona=MAYA, question="Which channels?", quick_replies=["Bluesky"]),
    NeedsApproval(
        approval_id="a1",
        team_id="t1",
        task_id="k1",
        task_type="social_post",
        persona=MAYA,
        preview="We launch Friday!",
        planned_action=PostSocial(text="We launch Friday!"),
        check_confidence=0.91,
    ),
    NeedsApproval(
        approval_id="a2",
        team_id="t1",
        task_id="k2",
        task_type="competitor_check",
        persona=MAYA,
        preview="Competitor summary",
        planned_action=None,
        check_confidence=0.8,
    ),
    ActionDone(
        action_id="x1",
        team_id="t1",
        task_id="k1",
        summary="Posted to Bluesky",
        url="https://bsky.app/profile/x/post/1",
        autonomous=False,
        undo_until=NOW + timedelta(minutes=10),
    ),
    ActionUndone(action_id="x1", team_id="t1", summary="Deleted the Bluesky post"),
    PromotionOffer(
        team_id="t1",
        task_type="social_post",
        persona=MAYA,
        current_level=AutonomyLevel.ACT_AFTER_APPROVAL,
        proposed_level=AutonomyLevel.ACT_AND_REPORT,
        evidence="You approved my last 5 posts without edits",
    ),
    LessonLearned(
        lesson_id="l1", team_id="t1", persona=MAYA, text="No emojis", business_wide=False
    ),
    TeamHired(team_id="t1", template="marketing", display_name="Marketing", personas=[MAYA]),
    OnboardingComplete(scope="business", team_id=None),
    Error(team_id=None, message="Something went wrong", recoverable=True),
]

event_adapter = TypeAdapter(Event)
action_adapter = TypeAdapter(PlannedAction)

@pytest.mark.parametrize("event", EVENTS, ids=lambda e: e.type)
def test_event_round_trips_through_json(event):
    parsed = event_adapter.validate_json(event_adapter.dump_json(event))
    assert type(parsed) is type(event)
    assert parsed == event

def test_every_event_type_is_covered():
    covered = {type(e) for e in EVENTS}
    union_members = set(Event.__origin__.__args__)
    assert covered == union_members

def test_unknown_event_type_is_rejected():
    with pytest.raises(ValidationError):
        event_adapter.validate_python({"type": "nope", "team_id": None})

@pytest.mark.parametrize(
    "action",
    [PostSocial(text="Hello"), SendEmail(to="a@b.co", subject="Hi", body="Body")],
    ids=lambda a: a.tool,
)
def test_planned_action_dispatches_on_tool(action):
    parsed = action_adapter.validate_json(action_adapter.dump_json(action))
    assert type(parsed) is type(action)

def test_social_post_is_capped_at_300_chars():
    with pytest.raises(ValidationError):
        PostSocial(text="x" * 301)

def test_approval_and_audit_entry_round_trip():
    approval = Approval(
        approval_id="a1",
        business_id="b1",
        team_id="t1",
        task_id="k1",
        task_type="newsletter",
        preview="Subject: Launch",
        planned_action=SendEmail(to="list@b.co", subject="Launch", body="We launch Friday"),
        check_confidence=0.85,
        created_at=NOW,
    )
    assert Approval.model_validate_json(approval.model_dump_json()) == approval

    entry = AuditEntry(
        action_id="x1",
        business_id="b1",
        team_id="t1",
        task_id="k1",
        tool="send_email",
        summary="Sent the newsletter",
        result=ActionResult(action_id="x1", ok=True),
        autonomous=False,
        approval_id="a1",
        at=NOW,
    )
    assert AuditEntry.model_validate_json(entry.model_dump_json()) == entry

def test_trust_promotes_after_five_by_default():
    trust = Trust(
        team_id="t1",
        task_type="social_post",
        level=AutonomyLevel.ACT_AFTER_APPROVAL,
        updated_at=NOW,
    )
    assert trust.promote_after == 5
    custom = trust.model_copy(update={"promote_after": 3})
    assert Trust.model_validate_json(custom.model_dump_json()) == custom

def test_trust_needs_at_least_one_approval_to_promote():
    with pytest.raises(ValidationError):
        Trust(
            team_id="t1",
            task_type="social_post",
            level=AutonomyLevel.ACT_AFTER_APPROVAL,
            promote_after=0,
            updated_at=NOW,
        )
