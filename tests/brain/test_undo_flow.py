from brain.flows.actions import execute_action
from brain.flows.undo import undo_action
from contract import ActionUndone, Error, PostSocial, SendEmail

POST = PostSocial(text="We launch Friday!")
EMAIL = SendEmail(to="list@b.co", subject="Launch", body="We launch Friday.")

async def run_action(deps, make_team, make_approval, planned_action=POST, streak=0):
    team = await make_team(streaks={"social_post": streak})
    approval = await make_approval(team, planned_action=planned_action)
    task = await deps.store.get_task(approval.task_id)
    done = await execute_action(
        deps, task, planned_action, approval_id=approval.approval_id, autonomous=False
    )
    return team, done

async def test_undo_inside_window_deletes_and_logs(
    deps, tools, clock, collect, make_team, make_approval
):
    team, done = await run_action(deps, make_team, make_approval)
    external_id = (await deps.store.get_action(done.action_id)).result.external_id
    clock.advance(minutes=5)

    events = await collect(undo_action(deps, "b1", done.action_id))

    assert len(events) == 1
    assert isinstance(events[0], ActionUndone)
    assert events[0].action_id == done.action_id
    assert events[0].team_id == team.team_id
    assert tools.calls[-1] == ("delete_social", {"business_id": "b1", "external_id": external_id})
    assert (await deps.store.get_action(done.action_id)).undone_at == clock()
    deletes = [e for e in deps.store.audit.values() if e.tool == "delete_social"]
    assert len(deletes) == 1
    assert deletes[0].result.ok

async def test_undo_resets_streak(deps, collect, make_team, make_approval):
    team, done = await run_action(deps, make_team, make_approval, streak=3)

    await collect(undo_action(deps, "b1", done.action_id))

    assert (await deps.store.get_trust(team.team_id, "social_post")).approval_streak == 0

async def test_undo_outside_window_yields_error(
    deps, tools, clock, collect, make_team, make_approval
):
    _, done = await run_action(deps, make_team, make_approval)
    clock.advance(minutes=11)

    events = await collect(undo_action(deps, "b1", done.action_id))

    assert [type(e) for e in events] == [Error]
    assert events[0].recoverable
    assert tools.calls[-1][0] == "post_social"

async def test_undo_twice_yields_error(deps, collect, make_team, make_approval):
    _, done = await run_action(deps, make_team, make_approval)
    await collect(undo_action(deps, "b1", done.action_id))

    events = await collect(undo_action(deps, "b1", done.action_id))

    assert [type(e) for e in events] == [Error]

async def test_undo_email_yields_error(deps, tools, collect, make_team, make_approval):
    _, done = await run_action(deps, make_team, make_approval, planned_action=EMAIL)

    events = await collect(undo_action(deps, "b1", done.action_id))

    assert [type(e) for e in events] == [Error]
    assert all(call[0] != "delete_social" for call in tools.calls)

async def test_undo_failed_delete_yields_error(deps, tools, collect, make_team, make_approval):
    _, done = await run_action(deps, make_team, make_approval)
    tools.fail = {"delete_social"}

    events = await collect(undo_action(deps, "b1", done.action_id))

    assert [type(e) for e in events] == [Error]
    assert (await deps.store.get_action(done.action_id)).undone_at is None

async def test_unknown_or_foreign_action_yields_error(deps, collect, make_team, make_approval):
    _, done = await run_action(deps, make_team, make_approval)

    for business_id, action_id in [("b1", "nope"), ("b2", done.action_id)]:
        events = await collect(undo_action(deps, business_id, action_id))
        assert [type(e) for e in events] == [Error]
