from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal

from brain.common import guarded
from brain.deps import Deps
from brain.flows.actions import execute_action
from brain.flows.autonomy import promotion_level, record_approve, reset_streak
from brain.templates.models import Template
from brain.templates.registries import POST_LIMIT
from contract import (
    Approval,
    ApprovalDecision,
    Error,
    Event,
    Persona,
    PlannedAction,
    PostSocial,
    PromotionOffer,
    Say,
    SendEmail,
    Task,
    TaskStatus,
    Trust,
)

LearnHook = Callable[[Approval, Literal["edit", "reject"], str], AsyncIterator[Event]]
ReviseHook = Callable[[Approval, str], AsyncIterator[Event]]

MAX_REVISIONS = 2

@dataclass
class Context:
    approval: Approval
    task: Task
    trust: Trust
    template: Template

    @property
    def lead(self) -> Persona:
        return self.template.lead.persona

def label(task_type: str) -> str:
    return task_type.replace("_", " ")

async def resolve_approval(
    deps: Deps,
    decision: ApprovalDecision,
    *,
    learn: LearnHook | None = None,
    revise: ReviseHook | None = None,
) -> AsyncIterator[Event]:
    async for event in guarded(_resolve(deps, decision, learn, revise)):
        yield event

async def _resolve(
    deps: Deps, decision: ApprovalDecision, learn: LearnHook | None, revise: ReviseHook | None
) -> AsyncIterator[Event]:
    approval = await deps.store.get_approval(decision.approval_id)
    if approval is None or approval.business_id != decision.business_id:
        yield Error(team_id=None, message="I couldn't find that draft.", recoverable=True)
        return
    if approval.status != "pending":
        yield Error(
            team_id=approval.team_id, message="That draft was already handled.", recoverable=True
        )
        return
    ctx = await _load(deps, approval)
    if ctx is None:
        yield Error(
            team_id=approval.team_id, message="I couldn't find that draft.", recoverable=True
        )
        return
    if decision.decision == "approve":
        steps = _approve(deps, ctx)
    elif decision.decision == "edit":
        steps = _edit(deps, ctx, decision.edited_text, decision.edited_action, learn)
    else:
        steps = _reject(deps, ctx, (decision.reason or "").strip(), learn, revise)
    async for event in steps:
        yield event

async def _load(deps: Deps, approval: Approval) -> Context | None:
    team = await deps.store.get_team(approval.team_id)
    task = await deps.store.get_task(approval.task_id)
    if team is None or task is None or team.template not in deps.templates:
        return None
    template = deps.templates[team.template]
    trust = await deps.store.get_trust(approval.team_id, approval.task_type)
    if trust is None:
        trust = Trust(
            team_id=approval.team_id,
            task_type=approval.task_type,
            level=template.task_types[approval.task_type].start_level,
            updated_at=deps.clock(),
        )
    return Context(approval=approval, task=task, trust=trust, template=template)

async def _approve(deps: Deps, ctx: Context) -> AsyncIterator[Event]:
    approval = ctx.approval
    done_text = "Great, marked it as done."
    if approval.planned_action is not None:
        outcome = await execute_action(
            deps,
            ctx.task,
            approval.planned_action,
            approval_id=approval.approval_id,
            autonomous=False,
        )
        yield outcome
        if isinstance(outcome, Error):
            return
        done_text = f"Done. {outcome.summary}."
    now = deps.clock()
    approved = approval.model_copy(update={"status": "approved", "resolved_at": now})
    await deps.store.save_approval(approved)
    await _save_task(deps, ctx.task, TaskStatus.DONE)
    trust = record_approve(ctx.trust, now)
    await deps.store.set_trust(trust)
    yield Say(
        team_id=approval.team_id, task_id=approval.task_id, persona=ctx.lead, text=done_text
    )

    recent = await deps.store.recent_approvals(
        approval.team_id, approval.task_type, trust.promote_after
    )
    proposed = promotion_level(
        trust,
        recent,
        ctx.template.max_level(approval.task_type),
        deps.settings.promotion_confidence,
    )
    if proposed is not None:
        yield PromotionOffer(
            team_id=approval.team_id,
            task_type=approval.task_type,
            persona=ctx.lead,
            current_level=trust.level,
            proposed_level=proposed,
            evidence=(
                f"You approved my last {trust.promote_after} {label(approval.task_type)} "
                "drafts without edits."
            ),
        )

async def _edit(
    deps: Deps,
    ctx: Context,
    edited_text: str | None,
    edited_action: PlannedAction | None,
    learn: LearnHook | None,
) -> AsyncIterator[Event]:
    """
    Contract v0.4: `edited_action`, when given, is the founder's whole version (an email's new
    subject and recipient too) and wins over `edited_text`. A draft without an action ignores it.
    """
    approval = ctx.approval
    planned = approval.planned_action
    if planned is None or edited_action is None:
        edited_action = None
    elif edited_action.tool != planned.tool:
        yield Error(
            team_id=approval.team_id,
            message="That edit doesn't match this draft. Try editing it again.",
            recoverable=True,
        )
        return
    text = (_text_of(edited_action) if edited_action else edited_text or "").strip()
    if not text:
        yield Error(
            team_id=approval.team_id,
            message="Send me your edited version and I'll use it.",
            recoverable=True,
        )
        return
    if isinstance(approval.planned_action, PostSocial) and len(text) > POST_LIMIT:
        message = (
            f"That's {len(text)} characters; Bluesky allows {POST_LIMIT}. Can you shorten it?"
        )
        yield Error(team_id=approval.team_id, message=message, recoverable=True)
        return
    action = edited_action or _edited_action(planned, text)
    if action is not None:
        outcome = await execute_action(
            deps, ctx.task, action, approval_id=approval.approval_id, autonomous=False
        )
        yield outcome
        if isinstance(outcome, Error):
            return
    now = deps.clock()
    edited = approval.model_copy(
        update={"status": "edited", "edited_text": text, "resolved_at": now}
    )
    await deps.store.save_approval(edited)
    await _save_task(deps, ctx.task, TaskStatus.DONE)
    await deps.store.set_trust(reset_streak(ctx.trust, now))
    if learn is not None:
        feedback = f"Original:\n{approval.preview}\n\nEdited:\n{_show(action, text)}"
        async for event in learn(edited, "edit", feedback):
            yield event
    yield Say(
        team_id=approval.team_id,
        task_id=approval.task_id,
        persona=ctx.lead,
        text="Thanks, I used your version.",
    )

def _edited_action(action: PlannedAction | None, text: str) -> PlannedAction | None:
    if isinstance(action, PostSocial):
        return action.model_copy(update={"text": text})
    if isinstance(action, SendEmail):
        return action.model_copy(update={"body": text})
    return None

def _text_of(action: PlannedAction) -> str:
    return action.text if isinstance(action, PostSocial) else action.body

def _show(action: PlannedAction | None, text: str) -> str:
    """
    The founder's version as reflect should see it: the whole email, not just its body.
    """
    if isinstance(action, SendEmail):
        return f"To: {action.to}\nSubject: {action.subject}\n\n{action.body}"
    return text

async def _reject(
    deps: Deps,
    ctx: Context,
    reason: str,
    learn: LearnHook | None,
    revise: ReviseHook | None,
) -> AsyncIterator[Event]:
    approval = ctx.approval
    now = deps.clock()
    rejected = approval.model_copy(
        update={"status": "rejected", "reason": reason or None, "resolved_at": now}
    )
    await deps.store.save_approval(rejected)
    await deps.store.set_trust(reset_streak(ctx.trust, now))
    if reason and learn is not None:
        feedback = f"Draft:\n{approval.preview}\n\nRejected because: {reason}"
        async for event in learn(rejected, "reject", feedback):
            yield event
    if reason and revise is not None and ctx.task.revisions < MAX_REVISIONS:
        async for event in revise(rejected, reason):
            yield event
        return
    await _save_task(deps, ctx.task, TaskStatus.REJECTED)
    yield Say(
        team_id=approval.team_id,
        task_id=approval.task_id,
        persona=ctx.lead,
        text="Okay, I've dropped that one.",
    )

async def _save_task(deps: Deps, task: Task, status: TaskStatus) -> None:
    updated = task.model_copy(update={"status": status, "updated_at": deps.clock()})
    await deps.store.save_task(updated)
