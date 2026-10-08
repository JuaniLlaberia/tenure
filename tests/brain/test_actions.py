from datetime import timedelta

from brain.flows.actions import execute_action
from contract import ActionDone, Error, PostSocial, SendEmail

POST = PostSocial(text="We launch Friday!")
EMAIL = SendEmail(to="list@b.co", subject="Launch", body="We launch Friday.")

async def make_task(deps, make_team, make_approval, planned_action):
    approval = await make_approval(await make_team(), planned_action=planned_action)
    return approval, await deps.store.get_task(approval.task_id)

async def test_post_runs_and_is_audited_with_undo_window(
    deps, tools, clock, make_team, make_approval
):
    approval, task = await make_task(deps, make_team, make_approval, POST)

    outcome = await execute_action(
        deps, task, POST, approval_id=approval.approval_id, autonomous=False
    )

    assert isinstance(outcome, ActionDone)
    assert outcome.summary == "Posted to Bluesky"
    assert outcome.url
    assert outcome.task_id == task.task_id
    assert outcome.undo_until == clock() + timedelta(minutes=10)
    assert tools.calls == [("post_social", {"business_id": "b1", "text": POST.text})]

    entry = await deps.store.get_action(outcome.action_id)
    assert entry.tool == "post_social"
    assert entry.approval_id == approval.approval_id
    assert entry.undo_until == outcome.undo_until
    assert entry.result.external_id
    assert not entry.autonomous

async def test_email_runs_and_is_never_undoable(deps, tools, make_team, make_approval):
    approval, task = await make_task(deps, make_team, make_approval, EMAIL)

    outcome = await execute_action(
        deps, task, EMAIL, approval_id=approval.approval_id, autonomous=False
    )

    assert isinstance(outcome, ActionDone)
    assert outcome.summary == "Sent the email to list@b.co"
    assert outcome.undo_until is None
    assert tools.calls == [
        (
            "send_email",
            {"business_id": "b1", "to": EMAIL.to, "subject": EMAIL.subject, "body": EMAIL.body},
        )
    ]
    assert (await deps.store.get_action(outcome.action_id)).undo_until is None

async def test_failed_action_is_audited_and_returns_error(deps, tools, make_team, make_approval):
    approval, task = await make_task(deps, make_team, make_approval, POST)
    tools.fail = {"post_social"}

    outcome = await execute_action(
        deps, task, POST, approval_id=approval.approval_id, autonomous=False
    )

    assert isinstance(outcome, Error)
    assert outcome.recoverable
    entries = list(deps.store.audit.values())
    assert len(entries) == 1
    assert not entries[0].result.ok
    assert entries[0].undo_until is None

async def test_autonomous_flag_is_recorded(deps, make_team, make_approval):
    _, task = await make_task(deps, make_team, make_approval, POST)

    outcome = await execute_action(deps, task, POST, approval_id=None, autonomous=True)

    assert outcome.autonomous
    entry = await deps.store.get_action(outcome.action_id)
    assert entry.autonomous
    assert entry.approval_id is None
