from brain.flows.approvals import resolve_approval
from contract import (
    ActionDone,
    ApprovalDecision,
    Error,
    PostSocial,
    SendEmail,
    TaskStatus,
)

EMAIL = SendEmail(to="list@b.co", subject="Launch", body="We launch Friday.")
EDITED = SendEmail(to="vip@b.co", subject="You're invited", body="Join us Friday at 6pm.")

def edit(approval, **fields):
    return ApprovalDecision(
        business_id=approval.business_id,
        approval_id=approval.approval_id,
        decision="edit",
        **fields,
    )

def sent(tools):
    return [kwargs for name, kwargs in tools.calls if name == "send_email"]

async def newsletter(make_team, make_approval):
    return await make_approval(await make_team(), task_type="newsletter", planned_action=EMAIL)

async def test_edited_action_sends_new_subject_and_recipient(
    deps, tools, collect, make_team, make_approval
):
    approval = await newsletter(make_team, make_approval)

    events = await collect(
        resolve_approval(deps, edit(approval, edited_text=EDITED.body, edited_action=EDITED))
    )

    assert any(isinstance(e, ActionDone) for e in events)
    assert sent(tools) == [
        {"business_id": "b1", "to": EDITED.to, "subject": EDITED.subject, "body": EDITED.body}
    ]
    stored = await deps.store.get_approval(approval.approval_id)
    assert stored.status == "edited"
    assert stored.edited_text == EDITED.body
    assert (await deps.store.get_task(approval.task_id)).status == TaskStatus.DONE

async def test_edited_action_alone_is_enough(deps, tools, collect, make_team, make_approval):
    approval = await newsletter(make_team, make_approval)

    await collect(resolve_approval(deps, edit(approval, edited_action=EDITED)))

    assert sent(tools)[0]["subject"] == EDITED.subject
    assert (await deps.store.get_approval(approval.approval_id)).edited_text == EDITED.body

async def test_edited_action_wins_over_a_different_edited_text(
    deps, tools, collect, make_team, make_approval
):
    approval = await newsletter(make_team, make_approval)

    await collect(
        resolve_approval(deps, edit(approval, edited_text="Stale body", edited_action=EDITED))
    )

    assert sent(tools)[0]["body"] == EDITED.body
    assert (await deps.store.get_approval(approval.approval_id)).edited_text == EDITED.body

async def test_edited_action_must_match_the_draft_tool(
    deps, tools, collect, make_team, make_approval
):
    approval = await newsletter(make_team, make_approval)

    events = await collect(
        resolve_approval(deps, edit(approval, edited_action=PostSocial(text="Hi")))
    )

    assert [type(e) for e in events] == [Error]
    assert events[0].recoverable
    assert tools.calls == []
    assert (await deps.store.get_approval(approval.approval_id)).status == "pending"

async def test_edited_post_action_is_posted(deps, tools, collect, make_team, make_approval):
    approval = await make_approval(await make_team())

    await collect(
        resolve_approval(deps, edit(approval, edited_action=PostSocial(text="Friday. Be there.")))
    )

    assert tools.calls == [("post_social", {"business_id": "b1", "text": "Friday. Be there."})]

async def test_edited_action_on_a_draft_without_action_runs_nothing(
    deps, tools, collect, make_team, make_approval
):
    approval = await make_approval(
        await make_team(), task_type="competitor_check", planned_action=None
    )

    await collect(
        resolve_approval(deps, edit(approval, edited_text="Shorter", edited_action=EDITED))
    )

    assert tools.calls == []
    assert (await deps.store.get_approval(approval.approval_id)).status == "edited"

async def test_learning_sees_the_whole_edited_email(deps, collect, make_team, make_approval):
    approval = await newsletter(make_team, make_approval)
    seen = []

    async def learn(approval, source, feedback):
        seen.append(feedback)
        return
        yield

    await collect(resolve_approval(deps, edit(approval, edited_action=EDITED), learn=learn))

    for text in [EMAIL.subject, EMAIL.to, EDITED.subject, EDITED.to, EDITED.body]:
        assert text in seen[0]
