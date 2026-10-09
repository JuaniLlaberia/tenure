"""
Builds the dashboard's JSON from the store: one call returns everything the page shows.
"""

from datetime import datetime, timedelta
from typing import Any

from app.chat import ui
from app.store.base import AppStore
from contract import (
    Approval,
    AuditEntry,
    AutonomyLevel,
    Brain,
    Lesson,
    Persona,
    PostSocial,
    SendEmail,
    Task,
    Team,
    TemplateInfo,
)

WEEK = timedelta(days=7)
PROMOTE_AFTER_MAX = 20
COST_PER_MILLION_TOKENS = 9.0
LEVELS = list(AutonomyLevel)
LEVEL_NAMES = ["Drafts only", "Asks first", "Acts, then tells you", "On its own"]
STATUS_LABELS = {
    "planned": "Planned",
    "in_progress": "In progress",
    "waiting_approval": "Waiting for you",
    "done": "Done",
    "rejected": "Rejected",
    "failed": "Failed",
}
SOURCE_LABELS = {
    "business_onboarding": "Setup",
    "team_onboarding": "Onboarding",
    "edit": "Edited draft",
    "reject": "Rejected draft",
    "chat": "Said in chat",
}

async def build_overview(
    store: AppStore, brain: Brain, business_id: str, now: datetime
) -> dict[str, Any]:
    profile = await store.get_profile(business_id)
    templates = {t.name: t for t in brain.list_templates()}
    teams = await store.list_teams(business_id)
    team_names = {team.team_id: team.display_name for team in teams}
    team_by_id = {team.team_id: team for team in teams}
    tasks = await store.list_tasks(business_id)
    approvals = await store.list_approvals(business_id)
    actions = await store.list_actions(business_id)
    lessons = await store.list_all_lessons(business_id)
    titles = {task.task_id: task.title for task in tasks}
    pending = [a for a in approvals if a.status == "pending"]
    leads = [templates[t.template].personas[0].name for t in teams if t.template in templates]
    return {
        "business": {"name": profile.name if profile else "Your business"},
        "lead": leads[0] if len(leads) == 1 else None,
        "stats": _stats(tasks, approvals, actions, now),
        "teams": [await _team(store, team, templates.get(team.template)) for team in teams],
        "drafts": [
            await _draft(store, a, approvals, team_by_id, templates) for a in pending
        ],
        "tasks": [_task(task, team_names) for task in tasks],
        "lessons": [_lesson(lesson, team_names) for lesson in lessons],
        "activity": [_action(a, titles, now) for a in actions if a.tool != "delete_social"],
        "templates": [_template(t, teams) for t in templates.values()],
        "levels": LEVEL_NAMES,
        "promote_after_max": PROMOTE_AFTER_MAX,
        "now": now,
    }

def _stats(
    tasks: list[Task], approvals: list[Approval], actions: list[AuditEntry], now: datetime
) -> dict[str, Any]:
    since = now - WEEK
    recent_tasks = [task for task in tasks if task.updated_at >= since]
    resolved = [a for a in approvals if a.resolved_at and a.resolved_at >= since]
    clean = [a for a in resolved if a.status == "approved"]
    tokens = sum(task.tokens_used for task in recent_tasks)
    return {
        "waiting": sum(a.status == "pending" for a in approvals),
        "done_week": sum(task.status == "done" for task in recent_tasks),
        "actions_week": sum(a.tool != "delete_social" and a.at >= since for a in actions),
        "clean_rate": round(100 * len(clean) / len(resolved)) if resolved else None,
        "tokens_week": tokens,
        "cost_week": round(tokens * COST_PER_MILLION_TOKENS / 1_000_000, 2),
    }

async def _team(store: AppStore, team: Team, template: TemplateInfo | None) -> dict[str, Any]:
    trust = await store.list_trust(team.team_id)
    order = template.task_types if template else []
    trust.sort(key=lambda t: order.index(t.task_type) if t.task_type in order else len(order))
    return {
        "team_id": team.team_id,
        "name": team.display_name,
        "hired_at": team.created_at,
        "onboarded": team.onboarded,
        "people": [p.model_dump() for p in template.personas] if template else [],
        "promote_after": max((t.promote_after for t in trust), default=5),
        "trust": [
            {
                "task_type": t.task_type,
                "label": ui.task_title(t.task_type),
                "level": LEVELS.index(t.level),
                "streak": t.approval_streak,
                "promote_after": t.promote_after,
            }
            for t in trust
        ],
    }

async def _draft(
    store: AppStore,
    approval: Approval,
    approvals: list[Approval],
    teams: dict[str, Team],
    templates: dict[str, TemplateInfo],
) -> dict[str, Any]:
    team = teams.get(approval.team_id)
    template = templates.get(team.template) if team else None
    personas = template.personas if template else []
    task = await store.get_task(approval.task_id)
    trust = await store.get_trust(approval.team_id, approval.task_type)
    rules = await store.list_lessons(approval.business_id, approval.team_id, approval.task_type)
    rejected = [
        a for a in approvals
        if a.task_id == approval.task_id and a.status == "rejected" and a.reason
    ]
    action = approval.planned_action
    return {
        "approval_id": approval.approval_id,
        "team_id": approval.team_id,
        "team": team.display_name if team else "",
        "lead": personas[0].name if personas else "The team",
        "type": ui.task_title(approval.task_type),
        "kind": "post" if isinstance(action, PostSocial) else "email" if action else "draft",
        "title": task.title if task else ui.task_title(approval.task_type),
        "asked": task.brief if task else None,
        "steps": [_step(step, personas) for step in (task.steps if task else [])],
        "revision": task.revisions if task else 0,
        "revised_after": rejected[0].reason if rejected else None,
        "preview": approval.preview,
        "text": action.text if isinstance(action, PostSocial) else action.body if action else None,
        "to": action.to if isinstance(action, SendEmail) else None,
        "subject": action.subject if isinstance(action, SendEmail) else None,
        "check": round(approval.check_confidence * 100),
        "rules": [lesson.text for lesson in rules],
        "streak": trust.approval_streak if trust else 0,
        "promote_after": trust.promote_after if trust else 5,
        "created_at": approval.created_at,
    }

def _step(step: str, personas: list[Persona]) -> str:
    for persona in personas[1:]:
        if step.lower() in persona.role.lower():
            return f"{persona.name} ({persona.role.lower()})"
    return step.replace("_", " ").capitalize()

def _task(task: Task, teams: dict[str, str]) -> dict[str, Any]:
    return {
        "title": task.title,
        "team": teams.get(task.team_id, ""),
        "type": ui.task_title(task.task_type),
        "status": task.status.value,
        "status_label": STATUS_LABELS.get(task.status.value, task.status.value),
        "revisions": task.revisions,
        "tokens": task.tokens_used,
        "updated_at": task.updated_at,
    }

def _lesson(lesson: Lesson, teams: dict[str, str]) -> dict[str, Any]:
    team = teams.get(lesson.team_id or "", "One team")
    scope = "All teams" if lesson.team_id is None else f"{team} only"
    return {
        "lesson_id": lesson.lesson_id,
        "text": lesson.text,
        "kind": lesson.kind,
        "scope": scope,
        "business_wide": lesson.team_id is None,
        "source": SOURCE_LABELS.get(lesson.source, lesson.source),
        "only": ui.task_title(lesson.task_type) if lesson.task_type else None,
    }

def _action(entry: AuditEntry, titles: dict[str, str], now: datetime) -> dict[str, Any]:
    undoable = entry.undo_until is not None and entry.undone_at is None and now <= entry.undo_until
    return {
        "action_id": entry.action_id,
        "at": entry.at,
        "summary": entry.summary,
        "task": titles.get(entry.task_id),
        "url": entry.result.url,
        "ok": entry.result.ok,
        "autonomous": entry.autonomous,
        "approved": entry.approval_id is not None,
        "undo_until": entry.undo_until if undoable else None,
        "undone_at": entry.undone_at,
        "permanent": entry.undo_until is None,
    }

def _template(template: TemplateInfo, teams: list[Team]) -> dict[str, Any]:
    names = [p.name for p in template.personas]
    return {
        "name": template.name,
        "display_name": template.display_name,
        "description": template.description,
        "people": ui.join_names(names),
        "hired": any(team.template == template.name for team in teams),
    }
