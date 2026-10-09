"""
The chat versions of the dashboard's tabs: /drafts, /team, /schedules, /knowledge,
/activity and /spend. Pure functions from store records to an HTML text and buttons.
"""

from datetime import datetime
from html import escape

from app.chat.port import Button, Keyboard
from app.chat.ui import (
    FORGET,
    LEVEL_WORDS,
    RUN_NOW,
    STOP,
    TURN_ON,
    UNDO,
    cadence_words,
    clip,
    task_title,
    text,
    when,
)
from app.web.overview import model_name
from contract import (
    Approval,
    AuditEntry,
    AutonomyLevel,
    Lesson,
    ModelUsage,
    Schedule,
    Task,
    TaskStatus,
    Team,
    Trust,
)

PAGE = 5
LOWER = "tl"
LOWER_YES = "ty"
LOWER_NO = "tn"
THRESHOLD = "th"
PAGE_TO = "pg"
LEVELS = list(AutonomyLevel)
PROMOTE_AFTER_MAX = 20
NOTHING_WAITS = "Nothing waits for you right now."
NO_TEAMS = "No teams yet. Hire one with /hire."
NO_SCHEDULES = (
    "No schedules yet. Ask a team in its topic, for example: "
    "\"Every Monday at 9, research a topic and propose a newsletter.\""
)
NO_LESSONS = (
    "Nothing learned yet. Edits, rejections with a reason and chat feedback become lessons."
)
NO_ACTIONS = "Nothing has gone out yet."
NO_SPEND = "No model calls this week."

def _pages(total: int) -> int:
    return max(1, (total + PAGE - 1) // PAGE)

def _pager(kind: str, page: int, total: int) -> list[Button]:
    buttons = []
    if page > 0:
        buttons.append(Button("← Back", f"{PAGE_TO}:{kind}:{page - 1}"))
    if page + 1 < _pages(total):
        buttons.append(Button("Next →", f"{PAGE_TO}:{kind}:{page + 1}"))
    return buttons

def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"

def drafts_view(
    approvals: list[Approval],
    tasks: dict[str, Task],
    links: dict[str, str],
    teams: dict[str, str],
) -> tuple[str, Keyboard]:
    if not approvals:
        return NOTHING_WAITS, []
    count = len(approvals)
    verb = "waits" if count == 1 else "wait"
    lines = [f"<b>{_plural(count, 'draft')} {verb} for you</b>"]
    keyboard: Keyboard = []
    for approval in approvals:
        task = tasks.get(approval.task_id)
        label = task_title(approval.task_type)
        title = f"{label} · {clip(task.title, 40)}" if task else label
        action = approval.planned_action
        images = approval.media or (action.images if action else [])
        extra = f" · {_plural(len(images), 'image')}" if images else ""
        team = teams.get(approval.team_id)
        lines.append(f"{text(title)}{extra}" + (f" <i>({text(team)})</i>" if team else ""))
        if approval.approval_id in links:
            keyboard.append([Button(f"→ {clip(title, 40)}", url=links[approval.approval_id])])
    return "\n".join(lines), keyboard

def team_view(team: Team, people: list[str], trust: list[Trust]) -> tuple[str, Keyboard]:
    lines = [f"<b>{text(team.display_name)}</b> · {text(', '.join(people))}"]
    keyboard: Keyboard = []
    for row in trust:
        progress = ""
        if row.level not in (AutonomyLevel.AUTONOMOUS,):
            progress = f" · {row.approval_streak}/{row.promote_after} clean approvals"
        title = text(task_title(row.task_type))
        lines.append(f"<b>{title}:</b> {LEVEL_WORDS[row.level]}{progress}")
        if LEVELS.index(row.level) > 0:
            keyboard.append([
                Button(
                    f"▾ Lower trust: {task_title(row.task_type)}",
                    f"{LOWER}:{team.team_id}:{row.task_type}",
                )
            ])
    if trust:
        after = trust[0].promote_after
        lines.append(f"\nI ask for more autonomy after {_plural(after, 'clean approval')}.")
        keyboard.append([
            Button("−", f"{THRESHOLD}:{team.team_id}:{max(1, after - 1)}"),
            Button(f"Offer after {after}", f"{THRESHOLD}:{team.team_id}:{after}"),
            Button("+", f"{THRESHOLD}:{team.team_id}:{min(PROMOTE_AFTER_MAX, after + 1)}"),
        ])
    return "\n".join(lines), keyboard

def lower_confirm(team: Team, trust: Trust) -> tuple[str, Keyboard]:
    lower = LEVELS[LEVELS.index(trust.level) - 1]
    title = task_title(trust.task_type)
    body = (
        f"<b>{text(title)}:</b> go from “{LEVEL_WORDS[trust.level]}” back to "
        f"“{LEVEL_WORDS[lower]}”? The streak starts over."
    )
    return body, [[
        Button("Yes, lower it", f"{LOWER_YES}:{team.team_id}:{trust.task_type}"),
        Button("Cancel", f"{LOWER_NO}:{team.team_id}"),
    ]]

def schedules_view(
    schedules: list[Schedule], teams: dict[str, str], running: frozenset[str]
) -> tuple[str, Keyboard]:
    if not schedules:
        return NO_SCHEDULES, []
    lines = ["<b>Schedules</b>"]
    keyboard: Keyboard = []
    for schedule in schedules:
        title = text(schedule.title)
        team = text(teams.get(schedule.team_id, ""))
        if not schedule.active:
            lines.append(f"<s>🔁 {title}</s> · {team} · stopped")
            label = f"↻ Turn on {clip(schedule.title, 30)}"
            keyboard.append([Button(label, f"{TURN_ON}:{schedule.schedule_id}")])
            continue
        if schedule.schedule_id in running:
            state = "running now"
        elif schedule.next_run_at:
            state = f"next {when(schedule.next_run_at, schedule.cadence.timezone)}"
        else:
            state = "being scheduled"
        lines.append(f"🔁 <b>{title}</b> · {team}\n{text(cadence_words(schedule))} · {state}")
        keyboard.append([
            Button(f"▶ Run {clip(schedule.title, 24)}", f"{RUN_NOW}:{schedule.schedule_id}"),
            Button("■ Stop", f"{STOP}:{schedule.schedule_id}"),
        ])
    return "\n".join(lines), keyboard

def knowledge_view(
    lessons: list[Lesson], teams: dict[str, str], scope: str, page: int
) -> tuple[str, Keyboard]:
    if not lessons:
        return NO_LESSONS, []
    page = min(page, _pages(len(lessons)) - 1)
    shown = lessons[page * PAGE : (page + 1) * PAGE]
    count = len(lessons)
    lines = [f"<b>What {text(scope)} knows about you</b> · {_plural(count, 'lesson')}"]
    for n, lesson in enumerate(shown, start=page * PAGE + 1):
        where = teams.get(lesson.team_id, "all teams") if lesson.team_id else "all teams"
        lines.append(f"{n}. {text(lesson.text)} <i>({text(where)})</i>")
    forget = [
        Button(f"Forget {n}", f"{FORGET}:{lesson.lesson_id}")
        for n, lesson in enumerate(shown, start=page * PAGE + 1)
    ]
    keyboard = [forget]
    pager = _pager("knowledge", page, len(lessons))
    if pager:
        keyboard.append(pager)
    return "\n".join(lines), keyboard

def _clock(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}, {moment.hour}:{moment.minute:02d} UTC"

def activity_view(
    actions: list[AuditEntry], tasks: list[Task], now: datetime, page: int
) -> tuple[str, Keyboard]:
    page = min(page, _pages(len(actions)) - 1)
    shown = actions[page * PAGE : (page + 1) * PAGE]
    counts = {status: sum(t.status == status for t in tasks) for status in TaskStatus}
    lines = [
        f"<b>Tasks this week:</b> {counts[TaskStatus.DONE]} done · "
        f"{counts[TaskStatus.WAITING_APPROVAL]} waiting for you · "
        f"{counts[TaskStatus.FAILED]} failed"
    ]
    keyboard: Keyboard = []
    if not actions:
        lines.append(NO_ACTIONS)
    else:
        lines.append("<b>Last actions</b>")
    for entry in shown:
        line = f"{_clock(entry.at)} · {text(entry.summary)}"
        if entry.result.url:
            line += f' · <a href="{escape(entry.result.url, quote=True)}">View</a>'
        if entry.undone_at:
            line = f"<s>{line}</s> · undone"
        lines.append(line)
        if entry.undo_until and not entry.undone_at and now < entry.undo_until:
            minutes = max(1, int((entry.undo_until - now).total_seconds() // 60))
            label = f"↩ Undo the {_clock(entry.at)} post ({minutes} min)"
            keyboard.append([Button(label, f"{UNDO}:{entry.action_id}")])
    pager = _pager("activity", page, len(actions))
    if pager:
        keyboard.append(pager)
    return "\n".join(lines), keyboard

def spend_view(usage: list[ModelUsage]) -> str:
    if not usage:
        return NO_SPEND
    by_model: dict[str, list[ModelUsage]] = {}
    for row in usage:
        by_model.setdefault(row.model, []).append(row)
    billed = sum(row.cost or 0 for row in usage)
    tokens = sum(row.input_tokens + row.output_tokens for row in usage)
    lines = [f"<b>Spend this week:</b> ${billed:.2f} billed · {tokens:,} tokens"]
    ranked = sorted(by_model.items(), key=lambda item: -sum(r.cost or 0 for r in item[1]))
    for model, rows in ranked:
        cost = sum(r.cost or 0 for r in rows)
        reported = any(r.cost is not None for r in rows)
        purpose = rows[0].purpose
        money = f"${cost:.2f}" if reported else "not reported"
        detail = f" · {text(purpose)}" if purpose else ""
        lines.append(f"{text(model_name(model))}{detail} · {len(rows)} calls · {money}")
    return "\n".join(lines)
