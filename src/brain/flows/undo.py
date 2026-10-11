from collections.abc import AsyncIterator

from brain.common import guarded
from brain.deps import Deps
from brain.flows.autonomy import back_to_asking, gate, reset_streak
from contract import ActionUndone, AuditEntry, Error, Event, Say

UNDONE = "Deleted the Bluesky post"
ASKING_AGAIN = "Undone. I'll ask before posting again; I can earn it back with a few approvals."

async def undo_action(deps: Deps, business_id: str, action_id: str) -> AsyncIterator[Event]:
    async for event in guarded(_undo(deps, business_id, action_id)):
        yield event

async def _undo(deps: Deps, business_id: str, action_id: str) -> AsyncIterator[Event]:
    entry = await deps.store.get_action(action_id)
    if entry is None or entry.business_id != business_id:
        yield Error(team_id=None, message="I couldn't find that action.", recoverable=True)
        return
    problem = _cannot_undo(entry, deps.clock())
    if problem:
        yield Error(team_id=entry.team_id, message=problem, recoverable=True)
        return

    result = await deps.tools.delete_social(business_id, entry.result.external_id)
    now = deps.clock()
    await deps.store.log_action(
        AuditEntry(
            action_id=result.action_id,
            business_id=business_id,
            team_id=entry.team_id,
            task_id=entry.task_id,
            tool="delete_social",
            summary=UNDONE if result.ok else "Failed to delete the Bluesky post",
            result=result,
            autonomous=False,
            approval_id=entry.approval_id,
            at=now,
        )
    )
    if not result.ok:
        yield Error(
            team_id=entry.team_id,
            message=f"I couldn't delete the post: {result.error}",
            recoverable=True,
        )
        return
    await deps.store.log_action(entry.model_copy(update={"undone_at": now}))
    lowered = await _lower_trust(deps, entry)
    yield ActionUndone(action_id=entry.action_id, team_id=entry.team_id, summary=UNDONE)
    if lowered:
        team = await deps.store.get_team(entry.team_id)
        template = deps.templates.get(team.template) if team else None
        if template is not None:
            yield Say(team_id=entry.team_id, persona=template.lead.persona, text=ASKING_AGAIN)

def _cannot_undo(entry: AuditEntry, now) -> str | None:
    if entry.undone_at is not None:
        return "That was already undone."
    if entry.undo_until is None or not entry.result.external_id:
        return "That one can't be undone."
    if now > entry.undo_until:
        return "The 10-minute undo window has passed."
    return None

async def _lower_trust(deps: Deps, entry: AuditEntry) -> bool:
    """
    Undo resets the streak. When the team had acted on its own (no approval), it also goes
    back to asking first; returns whether it did.
    """
    task = await deps.store.get_task(entry.task_id)
    if task is None:
        return False
    trust = await deps.store.get_trust(entry.team_id, task.task_type)
    if trust is None:
        return False
    if entry.approval_id is None and gate(trust.level) != "approval":
        await deps.store.set_trust(back_to_asking(trust, deps.clock()))
        return True
    await deps.store.set_trust(reset_streak(trust, deps.clock()))
    return False
