from brain.fakes import InMemoryStore
from brain.flows.approvals import resolve_approval
from contract import (
    ActionDone,
    ApprovalDecision,
    AutonomyLevel,
    Error,
    Persona,
    PostSocial,
    PromotionOffer,
    Say,
    SendEmail,
    TaskStatus,
)

POST = PostSocial(text="We launch Friday!")
EMAIL = SendEmail(to="list@b.co", subject="Launch", body="We launch Friday.")

def decide(approval, decision, **fields):
    return ApprovalDecision(
        business_id=approval.business_id,
        approval_id=approval.approval_id,
        decision=decision,
        **fields,
    )

def types(events):
    return [type(event) for event in events]

HOOK = Persona(name="Test", role="Hook")

class Hooks:
    def __init__(self):
        self.order = []
        self.learned = []
        self.revised = []

    async def learn(self, approval, source, feedback):
        self.order.append("learn")
        self.learned.append((approval.approval_id, source, feedback))
        yield Say(team_id=approval.team_id, persona=HOOK, text="learned")

    async def revise(self, approval, reason):
        self.order.append("revise")
        self.revised.append((approval.approval_id, reason))
        yield Say(team_id=approval.team_id, persona=HOOK, text="revised")

async def test_approve_runs_action_and_marks_done(deps, collect, make_team, make_approval):
    approval = await make_approval(await make_team())

    events = await collect(resolve_approval(deps, decide(approval, "approve")))

    assert types(events) == [ActionDone, Say]
    assert events[1].persona.name == "Maya"
    stored = await deps.store.get_approval(approval.approval_id)
    assert stored.status == "approved"
    assert stored.resolved_at == deps.clock()
    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.DONE
    entry = await deps.store.get_action(events[0].action_id)
    assert entry.approval_id == approval.approval_id
    assert not entry.autonomous

async def test_approve_increments_streak(deps, collect, make_team, make_approval):
    team = await make_team()
    approval = await make_approval(team)

    await collect(resolve_approval(deps, decide(approval, "approve")))

    assert (await deps.store.get_trust(team.team_id, "social_post")).approval_streak == 1

async def test_approve_without_planned_action_runs_nothing(
    deps, tools, collect, make_team, make_approval
):
    approval = await make_approval(
        await make_team(), task_type="competitor_check", planned_action=None
    )

    events = await collect(resolve_approval(deps, decide(approval, "approve")))

    assert types(events) == [Say]
    assert tools.calls == []
    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.DONE

async def test_approve_offers_promotion_after_five(deps, collect, make_team, make_approval):
    team = await make_team(streaks={"social_post": 4})
    for _ in range(4):
        await make_approval(team, status="approved", confidence=0.9)
    approval = await make_approval(team, confidence=0.9)

    events = await collect(resolve_approval(deps, decide(approval, "approve")))

    offer = events[-1]
    assert isinstance(offer, PromotionOffer)
    assert offer.team_id == team.team_id
    assert offer.task_type == "social_post"
    assert offer.persona.name == "Maya"
    assert offer.current_level == AutonomyLevel.ACT_AFTER_APPROVAL
    assert offer.proposed_level == AutonomyLevel.ACT_AND_REPORT
    assert offer.evidence
    assert (await deps.store.get_trust(team.team_id, "social_post")).approval_streak == 5

async def test_approve_failed_action_keeps_approval_pending(
    deps, tools, collect, make_team, make_approval
):
    team = await make_team()
    approval = await make_approval(team)
    tools.fail = {"post_social"}

    events = await collect(resolve_approval(deps, decide(approval, "approve")))

    assert types(events) == [Error]
    assert events[0].recoverable
    assert (await deps.store.get_approval(approval.approval_id)).status == "pending"
    task = await deps.store.get_task(approval.task_id)
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert (await deps.store.get_trust(team.team_id, "social_post")).approval_streak == 0

async def test_edit_posts_edited_text_and_resets_streak(
    deps, tools, collect, make_team, make_approval
):
    team = await make_team(streaks={"social_post": 3})
    approval = await make_approval(team)

    events = await collect(
        resolve_approval(deps, decide(approval, "edit", edited_text="Friday. Be there."))
    )

    assert ActionDone in types(events)
    assert tools.calls == [("post_social", {"business_id": "b1", "text": "Friday. Be there."})]
    stored = await deps.store.get_approval(approval.approval_id)
    assert stored.status == "edited"
    assert stored.edited_text == "Friday. Be there."
    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.DONE
    assert (await deps.store.get_trust(team.team_id, "social_post")).approval_streak == 0

async def test_edit_email_keeps_to_and_subject(deps, tools, collect, make_team, make_approval):
    approval = await make_approval(
        await make_team(), task_type="newsletter", planned_action=EMAIL
    )

    await collect(resolve_approval(deps, decide(approval, "edit", edited_text="New body")))

    sent = {"business_id": "b1", "to": EMAIL.to, "subject": EMAIL.subject, "body": "New body"}
    assert tools.calls == [("send_email", sent)]

async def test_edit_over_300_chars_yields_error(deps, tools, collect, make_team, make_approval):
    approval = await make_approval(await make_team())

    events = await collect(resolve_approval(deps, decide(approval, "edit", edited_text="x" * 301)))

    assert types(events) == [Error]
    assert events[0].recoverable
    assert tools.calls == []
    assert (await deps.store.get_approval(approval.approval_id)).status == "pending"

async def test_edit_calls_learn_hook_with_original_and_edited(
    deps, collect, make_team, make_approval
):
    approval = await make_approval(await make_team())
    hooks = Hooks()

    events = await collect(
        resolve_approval(
            deps, decide(approval, "edit", edited_text="Friday. Be there."), learn=hooks.learn
        )
    )

    approval_id, source, feedback = hooks.learned[0]
    assert (approval_id, source) == (approval.approval_id, "edit")
    assert POST.text in feedback
    assert "Friday. Be there." in feedback
    assert any(isinstance(e, Say) and e.text == "learned" for e in events)

async def test_edit_without_text_yields_error(deps, tools, collect, make_team, make_approval):
    approval = await make_approval(await make_team())

    events = await collect(resolve_approval(deps, decide(approval, "edit")))

    assert types(events) == [Error]
    assert tools.calls == []
    assert (await deps.store.get_approval(approval.approval_id)).status == "pending"

async def test_reject_without_reason_marks_rejected(
    deps, tools, collect, make_team, make_approval
):
    team = await make_team(streaks={"social_post": 3})
    approval = await make_approval(team)
    hooks = Hooks()

    events = await collect(
        resolve_approval(
            deps, decide(approval, "reject"), learn=hooks.learn, revise=hooks.revise
        )
    )

    assert isinstance(events[-1], Say)
    assert hooks.order == []
    assert tools.calls == []
    assert (await deps.store.get_approval(approval.approval_id)).status == "rejected"
    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.REJECTED
    assert (await deps.store.get_trust(team.team_id, "social_post")).approval_streak == 0

async def test_reject_with_reason_calls_learn_then_revise(
    deps, collect, make_team, make_approval
):
    approval = await make_approval(await make_team())
    hooks = Hooks()

    events = await collect(
        resolve_approval(
            deps,
            decide(approval, "reject", reason="Too salesy"),
            learn=hooks.learn,
            revise=hooks.revise,
        )
    )

    assert hooks.order == ["learn", "revise"]
    assert hooks.learned[0][1] == "reject"
    assert "Too salesy" in hooks.learned[0][2]
    assert POST.text in hooks.learned[0][2]
    assert hooks.revised == [(approval.approval_id, "Too salesy")]
    assert [e.text for e in events if isinstance(e, Say)][-1] == "revised"
    stored = await deps.store.get_approval(approval.approval_id)
    assert stored.status == "rejected"
    assert stored.reason == "Too salesy"
    task = await deps.store.get_task(approval.task_id)
    assert task.status != TaskStatus.REJECTED

async def test_reject_with_reason_and_no_revisions_left_marks_rejected(
    deps, collect, make_team, make_approval
):
    approval = await make_approval(await make_team(), revisions=2)
    hooks = Hooks()

    await collect(
        resolve_approval(
            deps,
            decide(approval, "reject", reason="Still too salesy"),
            learn=hooks.learn,
            revise=hooks.revise,
        )
    )

    assert hooks.order == ["learn"]
    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.REJECTED

async def test_reject_with_reason_without_revise_hook_marks_rejected(
    deps, collect, make_team, make_approval
):
    approval = await make_approval(await make_team())

    await collect(resolve_approval(deps, decide(approval, "reject", reason="Too salesy")))

    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.REJECTED

async def test_already_resolved_yields_error(deps, tools, collect, make_team, make_approval):
    approval = await make_approval(await make_team())
    await collect(resolve_approval(deps, decide(approval, "approve")))

    events = await collect(resolve_approval(deps, decide(approval, "approve")))

    assert types(events) == [Error]
    assert events[0].recoverable
    assert len(tools.calls) == 1

async def test_unknown_or_foreign_approval_yields_error(
    deps, tools, collect, make_team, make_approval
):
    approval = await make_approval(await make_team())
    unknown = decide(approval, "approve").model_copy(update={"approval_id": "nope"})
    foreign = decide(approval, "approve").model_copy(update={"business_id": "b2"})

    for decision in [unknown, foreign]:
        events = await collect(resolve_approval(deps, decision))
        assert types(events) == [Error]
        assert events[0].recoverable
    assert tools.calls == []

async def test_flow_never_raises(deps, collect, make_team, make_approval):
    approval = await make_approval(await make_team())

    class BrokenStore(InMemoryStore):
        async def get_approval(self, approval_id):
            raise RuntimeError("database down")

    deps.store = BrokenStore()

    events = await collect(resolve_approval(deps, decide(approval, "approve")))

    assert types(events) == [Error]
    assert events[0].recoverable
