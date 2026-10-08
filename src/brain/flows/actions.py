import logging

from brain.common import new_id
from brain.deps import Deps
from brain.flows.autonomy import UNDO_WINDOW
from contract import (
    ActionDone,
    ActionResult,
    AuditEntry,
    Error,
    PlannedAction,
    PostSocial,
    Task,
)

log = logging.getLogger(__name__)

async def execute_action(
    deps: Deps, task: Task, action: PlannedAction, *, approval_id: str | None, autonomous: bool
) -> ActionDone | Error:
    """
    Runs one real action and always writes it to the audit log, also when it fails.
    """
    if isinstance(action, PostSocial):
        summary, failure = "Posted to Bluesky", "post to Bluesky"
        result = await _run(deps.tools.post_social(task.business_id, action.text))
    else:
        summary, failure = f"Sent the email to {action.to}", "send the email"
        result = await _run(
            deps.tools.send_email(task.business_id, action.to, action.subject, action.body)
        )
    now = deps.clock()
    undoable = action.tool == "post_social" and result.ok
    undo_until = now + UNDO_WINDOW if undoable else None
    await deps.store.log_action(
        AuditEntry(
            action_id=result.action_id,
            business_id=task.business_id,
            team_id=task.team_id,
            task_id=task.task_id,
            tool=action.tool,
            summary=summary if result.ok else f"Failed to {failure}",
            result=result,
            autonomous=autonomous,
            approval_id=approval_id,
            undo_until=undo_until,
            at=now,
        )
    )
    if not result.ok:
        return Error(
            team_id=task.team_id,
            message=f"I couldn't {failure}: {result.error}",
            recoverable=True,
        )
    return ActionDone(
        action_id=result.action_id,
        team_id=task.team_id,
        task_id=task.task_id,
        summary=summary,
        url=result.url,
        autonomous=autonomous,
        undo_until=undo_until,
    )

async def _run(call) -> ActionResult:
    try:
        return await call
    except Exception as error:
        log.exception("Action tool raised")
        return ActionResult(action_id=new_id(), ok=False, error=type(error).__name__)
