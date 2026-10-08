"""
Telegram texts and keyboards for every brain event, as approved in the mockup.
Pure functions: HTML strings (Telegram parse mode) and Button rows.
"""

from html import escape

from app.chat.port import Button, Keyboard
from contract import (
    ActionDone,
    ActionUndone,
    Ask,
    AutonomyLevel,
    Error,
    LessonLearned,
    NeedsApproval,
    Persona,
    PlannedAction,
    PostSocial,
    Progress,
    PromotionOffer,
    Say,
    SendEmail,
    TeamHired,
    TemplateInfo,
)

POST_LIMIT = 300
PREVIEW_LIMIT = 3000
EXPANDABLE_AFTER = 600

APPROVE = "ap"
EDIT = "ed"
REJECT = "rj"
REASON = "rr"
DROP = "rd"
UNDO = "un"
ANSWER = "qr"
HIRE = "hi"
PROMOTE_YES = "py"
PROMOTE_NO = "pn"
FORGET = "fg"

TASK_TITLES = {
    "social_post": "Bluesky post",
    "newsletter": "Newsletter",
    "invoice_reminder": "Payment reminder",
    "competitor_check": "Competitor check",
}
LEVEL_WORDS = {
    AutonomyLevel.DRAFT_ONLY: "I only draft",
    AutonomyLevel.ACT_AFTER_APPROVAL: "I ask before acting",
    AutonomyLevel.ACT_AND_REPORT: "I act, then tell you",
    AutonomyLevel.AUTONOMOUS: "I act on my own",
}

HELP = (
    "<b>What I can do</b>\n"
    "/start: set up your business (in the company group)\n"
    "/hire: hire a team, e.g. /hire marketing\n"
    "/cancel: stop an edit or a reason you started\n\n"
    "Talk to a team in its topic. Talk to Alex, your chief of staff, in General."
)
PRIVATE_HINT = (
    "Add me to your company group, turn on Topics in the group settings, "
    "make me an admin, then send /start there."
)
NEEDS_TOPICS = "Turn on Topics in the group settings first, then send /start again."
NOT_SET_UP = "Send /start to set me up first."
NOT_A_TEAM = "This topic doesn't belong to a team. Talk to Alex in General, or /hire a team."
NOTHING_TO_CANCEL = "There's nothing to cancel."
CANCELLED = "Okay, the draft is unchanged."
ALREADY_HANDLED = "Already handled."
GONE = "This is no longer available."
UNDO_CLOSED = "The 10-minute undo window has closed."
BROKEN = "⚠️ Something went wrong on my side. Please try again."
SETUP_DONE = "<b>Setup done.</b> Pick your first team:"
PICK_TEAM = "Which team do you want to hire?"

APPROVED = "✓ Approved"
EDITING = "✎ Editing: send your version below, or /cancel"
EDITED = "✎ Edited"
REJECTING = "✕ Rejecting: send your reason below, or /cancel"
REJECTED = "✕ Rejected"

def text(value: str) -> str:
    return escape(value, quote=False)

def header(persona: Persona) -> str:
    return f"<b>{text(persona.name)}</b> <i>· {text(persona.role)}</i>\n"

def with_footer(body: str, footer: str | None) -> str:
    return f"{body}\n\n<i>{text(footer)}</i>" if footer else body

def clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"

def join_names(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"

def say_text(event: Say) -> str:
    return header(event.persona) + text(event.text)

def status_text(event: Progress) -> str:
    return f"<i>{text(event.status)}</i>"

def ask_text(event: Ask) -> str:
    return header(event.persona) + text(event.question)

def ask_keyboard(key: str, replies: list[str]) -> Keyboard:
    buttons = [Button(reply, f"{ANSWER}:{key}:{i}") for i, reply in enumerate(replies)]
    if sum(len(reply) for reply in replies) <= 32:
        return [buttons] if buttons else []
    return [[button] for button in buttons]

def task_title(task_type: str) -> str:
    return TASK_TITLES.get(task_type, task_type.replace("_", " ").capitalize())

def approval_meta(action: PlannedAction | None) -> str:
    if isinstance(action, PostSocial):
        return f"Bluesky post · {len(action.text)}/{POST_LIMIT} characters"
    if isinstance(action, SendEmail):
        return f"Email to {action.to}"
    return "Draft only, nothing gets sent"

def approval_text(event: NeedsApproval) -> str:
    preview = clip(event.preview, PREVIEW_LIMIT)
    tag = "blockquote expandable" if len(preview) > EXPANDABLE_AFTER else "blockquote"
    meta = f"{approval_meta(event.planned_action)} · check {round(event.check_confidence * 100)}%"
    return (
        f"{header(event.persona)}Draft for approval: <b>{text(task_title(event.task_type))}</b>\n"
        f"<{tag}>{text(preview)}</blockquote>\n<i>{text(meta)}</i>"
    )

def approval_keyboard(approval_id: str) -> Keyboard:
    return [[
        Button("✓ Approve", f"{APPROVE}:{approval_id}"),
        Button("✎ Edit", f"{EDIT}:{approval_id}"),
        Button("✕ Reject", f"{REJECT}:{approval_id}"),
    ]]

def reject_keyboard(approval_id: str) -> Keyboard:
    return [[
        Button("Give a reason", f"{REASON}:{approval_id}"),
        Button("Just drop it", f"{DROP}:{approval_id}"),
    ]]

def edit_prompt(action: PlannedAction | None) -> str:
    if isinstance(action, PostSocial):
        return (
            f"Send your version of the post (max {POST_LIMIT} characters), or /cancel to keep "
            f"the draft. Current text, tap to copy:\n<code>{text(action.text)}</code>"
        )
    if isinstance(action, SendEmail):
        return (
            "Send the new email body. The recipient and subject stay the same. "
            f"/cancel keeps the draft. Current body:\n<pre>{text(action.body)}</pre>"
        )
    return "Send your version, or /cancel to keep the draft."

def too_long(length: int) -> str:
    return (
        f"That's {length} characters; Bluesky allows {POST_LIMIT}. "
        "Send a shorter version, or /cancel."
    )

def reason_prompt(persona: Persona) -> str:
    return (
        f"What should {text(persona.name)} change? Your next message here is the reason. "
        "/cancel to skip."
    )

def action_text(event: ActionDone) -> str:
    body = f"<b>{text(event.summary)}</b>"
    if event.url:
        body += f' · <a href="{escape(event.url, quote=True)}">View</a>'
    if event.autonomous:
        body += "\n<i>Done without asking, as you allowed.</i>"
    return body

def undo_keyboard(action_id: str) -> Keyboard:
    return [[Button("↩ Undo (10 min)", f"{UNDO}:{action_id}")]]

def undone_text(original: str, event: ActionUndone) -> str:
    return f"<s>{text(original)}</s>\n{text(event.summary)}."

def promotion_text(event: PromotionOffer) -> str:
    return (
        f"{header(event.persona)}{text(event.evidence)} Can I take the next step?\n\n"
        f"<b>Now:</b> {LEVEL_WORDS[event.current_level]}\n"
        f"<b>Next:</b> {LEVEL_WORDS[event.proposed_level]}\n\n"
        "You can take this back anytime from the dashboard."
    )

def promotion_keyboard(key: str) -> Keyboard:
    return [[
        Button("✓ Yes, go ahead", f"{PROMOTE_YES}:{key}"),
        Button("Not yet", f"{PROMOTE_NO}:{key}"),
    ]]

def promoted(task_type: str, level: AutonomyLevel) -> str:
    return f"✓ {task_title(task_type)}: {LEVEL_WORDS[level].lower()}"

def not_promoted(persona: Persona) -> str:
    return f"Not yet. {persona.name} keeps asking first."

def lesson_text(event: LessonLearned, team_name: str | None) -> str:
    scope = "All teams" if event.business_wide else f"{team_name or 'This'} team only"
    return f"{header(event.persona)}📌 <b>Learned:</b> {text(event.text)}\n<i>{text(scope)}</i>"

def forget_keyboard(lesson_id: str) -> Keyboard:
    return [[Button("Forget", f"{FORGET}:{lesson_id}")]]

def forgotten(persona: Persona) -> str:
    return f"Forgotten. {persona.name} won't use this anymore."

def roster_text(event: TeamHired) -> str:
    lines = [f"{text(p.name)} · {text(p.role)}" for p in event.personas]
    return f"<b>Your {text(event.display_name)} team</b>\n" + "\n".join(lines)

def hired_text(event: TeamHired) -> str:
    names = join_names([p.name for p in event.personas])
    verb = "is" if len(event.personas) == 1 else "are"
    name = text(event.display_name)
    return f"<b>{name} team hired.</b> {text(names)} {verb} waiting in the {name} topic."

def topic_link(chat_id: int, thread_id: int) -> str:
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{thread_id}"

def open_topic_keyboard(name: str, url: str) -> Keyboard:
    return [[Button(f"→ Open {name}", url=url)]]

def hire_keyboard(templates: list[TemplateInfo]) -> Keyboard:
    buttons = [Button(t.display_name, f"{HIRE}:{t.name}") for t in templates]
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

def hiring(name: str) -> str:
    return f"→ Hiring {name}"

def error_text(event: Error) -> str:
    body = f"⚠️ {text(event.message)}"
    if not event.recoverable:
        body += "\nUse /help if this keeps happening."
    return body
