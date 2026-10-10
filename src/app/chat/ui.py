"""
Telegram texts and keyboards for every brain event, as approved in the mockup.
Pure functions: HTML strings (Telegram parse mode) and Button rows.
"""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.chat.port import Button, Keyboard
from contract import (
    ActionDone,
    ActionUndone,
    Ask,
    AutonomyLevel,
    Error,
    FileRef,
    LessonLearned,
    NeedsApproval,
    Persona,
    PlannedAction,
    PostSocial,
    Progress,
    PromotionOffer,
    Say,
    Schedule,
    SendEmail,
    TeamHired,
    TemplateInfo,
)

POST_LIMIT = 300
PREVIEW_LIMIT = 3000
CAPTION_LIMIT = 1024
EXPANDABLE_AFTER = 600

APPROVE = "ap"
EDIT = "ed"
REJECT = "rj"
REASON = "rr"
DROP = "rd"
UNDO = "un"
ANSWER = "qr"
HIRE = "hi"
REPLACE = "rp"
KEEP = "rk"
PROMOTE_YES = "py"
PROMOTE_NO = "pn"
FORGET = "fg"
RUN_NOW = "sr"
EDIT_FIELD = "ef"
NEW_IMAGE = "ni"
IMAGE_REASON = "nr"
IMAGE_AGAIN = "ng"
EDIT_APPROVE = "ea"
EDIT_MORE = "em"
EDIT_UNDO = "eu"
STOP = "ss"
TURN_ON = "so"
PROMPT_CANCEL = "pc"
RETRY = "rt"
SCHEDULE = "sc"
SCHEDULE_AT = "sa"
SEND_NOW = "sn"
UNSCHEDULE = "su"
MESSAGE_LIMIT = 4000

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
    "/drafts: everything waiting for your OK\n"
    "/team: trust per task, lower it, and when the team asks for more\n"
    "/schedules: repeating work; run, stop or turn back on\n"
    "/knowledge: what the team knows about you, with Forget\n"
    "/activity: what went out, with Undo, and this week's tasks\n"
    "/spend: what the models cost this week\n"
    "/cancel: stop an edit or a reason you started\n"
    "/stop: stop what the team in this topic is working on\n"
    "/dashboard: get the dashboard link and a new password\n"
    "/dashboard_stop: turn the dashboard off; the next /dashboard gets a new link\n\n"
    "In a team's topic these show that team; in General, the whole business. "
    "Talk to a team in its topic, and to Alex, your chief of staff, in General. "
    "You can send voice notes, photos and files too."
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
ALREADY_HIRING = "That team is already being hired."
PICK_TEAM = "Which team do you want to hire?"
FILE_DURING_EDIT = (
    "I need text here. To drop an image from a draft, use the dashboard. "
    "/cancel stops the edit."
)
FILE_FAILED = "I couldn't download that file. Please send it again."

APPROVED = "✓ Approved"
EDITING = "✎ Editing: send your version below, or /cancel"
EDITING_BELOW = "✎ Editing below"
EDIT_UNDONE = "↺ Your changes are undone. The draft is back above."
APPROVED_YOURS = "✓ Approved your version"
EDITED = "✎ Edited"
REJECTING = "✕ Rejecting: send your reason below, or /cancel"
REJECTED = "✕ Rejected"
APPROVED_ON_DASHBOARD = "✓ Approved on the dashboard"
REJECTED_ON_DASHBOARD = "✕ Rejected on the dashboard"
NEW_IMAGE_ASK = "↻ New image: what should change?"
NEW_IMAGE_WAITING = "↻ New image: send what should change below, or /cancel"
NEW_IMAGE_REQUESTED = "↻ New image requested"
NEW_IMAGE_ON_DASHBOARD = "↻ New image requested on the dashboard"
NO_REVISIONS_LEFT = "I'm out of revisions on this one. Approve it as it is, or reject it."
IMAGE_REASON_PROMPT = (
    "What should change in the image? Your next message here is the reason. /cancel to skip."
)
EDITED_ON_DASHBOARD = "✎ Edited on the dashboard"
EDIT_TIMED_OUT = "The edit timed out. The draft is unchanged."
REASON_TIMED_OUT = "The reason timed out. The draft is waiting for you."
IMAGE_TIMED_OUT = "The image request timed out. The draft is waiting for you."
TIME_TIMED_OUT = "No time was set. The draft is waiting for you."
REVISED_BELOW = "↻ Revised below"
NEEDS_ADMIN = (
    "I need to be an admin who can <b>manage topics</b> to give each team its own topic.\n"
    "Open the group settings → Administrators → add me and turn on “Manage topics”. "
    "Then send /start again."
)
SETUP_FIRST = "Let's finish setting up first: answer Alex's question in General, then hire a team."
TOO_LONG_RUN = "⚠️ This took too long, so I stopped it. Nothing was sent. Please try again."
NOTHING_RUNNING = "Nothing is running here right now."
QUEUED_RUN = "⏳ Queued: runs after the current draft"
OTHER_TIME_PROMPT = "Send the time, like “Fri 18:00” or “16 Oct 9:30”. /cancel to skip."
BAD_TIME = "I couldn't read that time. Try “Fri 18:00” or “16 Oct 9:30”, or /cancel."
PAST_TIME = "That time has passed. Send a later one, or /cancel."
SENDING_NOW = "✓ Approved · sending now"
SCHEDULE_CANCELLED = "Schedule cancelled. The draft is waiting for you."

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

def approval_meta(action: PlannedAction | None, media: list[FileRef] | None = None) -> str:
    if isinstance(action, PostSocial):
        return f"Bluesky post · {len(action.text)}/{POST_LIMIT} characters"
    if isinstance(action, SendEmail):
        return f"Email to {action.to}"
    if media:
        return "Image only, nothing gets posted"
    return "Draft only, nothing gets sent"

def draft_images(event: NeedsApproval) -> list[FileRef]:
    if event.planned_action is not None:
        return list(event.planned_action.images)
    return list(event.media)

def images_meta(count: int, above: bool) -> str:
    if not count:
        return ""
    words = "1 image" if count == 1 else f"{count} images"
    return f" · {words} above" if above else f" · {words}"

def approval_text(event: NeedsApproval, images_above: bool = False) -> str:
    preview = clip(event.preview, PREVIEW_LIMIT)
    tag = "blockquote expandable" if len(preview) > EXPANDABLE_AFTER else "blockquote"
    pictures = images_meta(len(draft_images(event)), images_above)
    confidence = f"check {round(event.check_confidence * 100)}%"
    meta = f"{approval_meta(event.planned_action, event.media)}{pictures} · {confidence}"
    return (
        f"{header(event.persona)}Draft for approval: <b>{text(task_title(event.task_type))}</b>\n"
        f"<{tag}>{text(preview)}</blockquote>\n<i>{text(meta)}</i>"
    )

def approval_keyboard(
    approval_id: str,
    editable: bool = True,
    new_image: bool = False,
    timing: str | None = None,
) -> Keyboard:
    """
    `timing`: None for drafts without an action, "none" for an action with no send time
    (it can get one), "timed" for an action with a send time.
    """
    approve = Button("✓ Approve", f"{APPROVE}:{approval_id}")
    reject = Button("✕ Reject", f"{REJECT}:{approval_id}")
    row = [approve, Button("✎ Edit", f"{EDIT}:{approval_id}"), reject] if editable else [
        approve,
        reject,
    ]
    extra = [Button("↻ New image", f"{NEW_IMAGE}:{approval_id}")] if new_image else []
    if timing == "none":
        extra.append(Button("⏰ Schedule", f"{SCHEDULE}:{approval_id}"))
    elif timing == "timed":
        extra.append(Button("⏰ Change time", f"{SCHEDULE}:{approval_id}"))
        extra.append(Button("Send now", f"{SEND_NOW}:{approval_id}"))
    return [row, extra] if extra else [row]

def failed_keyboard(approval_id: str, editable: bool) -> Keyboard:
    retry = Button("↻ Try again", f"{RETRY}:{approval_id}")
    reject = Button("✕ Reject", f"{REJECT}:{approval_id}")
    if not editable:
        return [[retry, reject]]
    return [[retry, Button("✎ Edit", f"{EDIT}:{approval_id}"), reject]]

def failed_footer(message: str) -> str:
    return f"⚠️ {message.strip().rstrip('.')}. Nothing went out."

def failed_on_dashboard(message: str) -> str:
    return f"{failed_footer(message)} The draft is back in Drafts."

def error_line(message: str) -> str:
    return f"⚠️ {text(message)}"

def prompt_cancel_keyboard(approval_id: str) -> Keyboard:
    return [[Button("Cancel", f"{PROMPT_CANCEL}:{approval_id}")]]

def revising(reason: str) -> str:
    return f"↻ Revising: {clip(reason.strip(), 200)}"

def rejected_queued(name: str) -> str:
    return f"✕ Rejected · {name} revises it after the current draft"

def local_when(moment: datetime, timezone: str) -> str:
    local = moment.astimezone(ZoneInfo(timezone))
    return f"{local:%a} {local.day} {local:%b}, {local.hour}:{local.minute:02d}"

def goes_out(moment: datetime, timezone: str) -> str:
    return f"⏰ Goes out {local_when(moment, timezone)} ({_place(timezone)} time) once you approve"

def scheduled_footer(moment: datetime, timezone: str) -> str:
    return f"✓ Approved · ⏰ goes out {local_when(moment, timezone)}"

def when_prompt(timezone: str) -> str:
    return f"⏰ When should it go out? ({_place(timezone)} time)"

def when_keyboard(approval_id: str) -> Keyboard:
    def choice(label: str, key: str) -> Button:
        return Button(label, f"{SCHEDULE_AT}:{approval_id}:{key}")

    return [
        [choice("In 1 hour", "hour"), choice("Tomorrow 9:00", "tomorrow")],
        [choice("Monday 9:00", "monday"), choice("Other time", "other")],
        [choice("Cancel", "cancel")],
    ]

def scheduled_keyboard(approval_id: str) -> Keyboard:
    return [[
        Button("Send now", f"{SEND_NOW}:{approval_id}"),
        Button("Cancel schedule", f"{UNSCHEDULE}:{approval_id}"),
    ]]

def new_image_keyboard(approval_id: str) -> Keyboard:
    return [[
        Button("Say what to change", f"{IMAGE_REASON}:{approval_id}"),
        Button("Just try again", f"{IMAGE_AGAIN}:{approval_id}"),
    ]]

def made_images(event: NeedsApproval) -> bool:
    return any(image.source == "generated" for image in draft_images(event))

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

FIELD_NAMES = {"text": "text", "subject": "subject", "to": "recipient"}

def action_title(action: PlannedAction) -> str:
    return "Bluesky post" if isinstance(action, PostSocial) else "Email"

def edit_menu_text(action: PlannedAction) -> str:
    return (
        f"What do you want to change in the {action_title(action).lower()}? "
        "Your changes wait here until you approve them."
    )

def edit_menu_keyboard(
    approval_id: str, action: PlannedAction, original: PlannedAction
) -> Keyboard:
    fields = ["text"] if isinstance(action, PostSocial) else ["text", "subject", "to"]
    rows = [
        [Button(FIELD_NAMES[f].capitalize(), f"{EDIT_FIELD}:{approval_id}:{f}") for f in fields]
    ]
    kept = {image.file_id for image in action.images}
    removable = [
        Button(f"✕ Image {n}", f"{EDIT_FIELD}:{approval_id}:img{n}")
        for n, image in enumerate(original.images, start=1)
        if image.file_id in kept
    ]
    if removable:
        rows.append(removable)
    rows.append([Button("Cancel", f"{EDIT_UNDO}:{approval_id}")])
    return rows

def field_prompt(field: str, action: PlannedAction) -> str:
    if field == "text" and isinstance(action, PostSocial):
        return (
            f"Send the new post text (max {POST_LIMIT} characters), or /cancel. "
            f"Current text, tap to copy:\n<code>{text(action.text)}</code>"
        )
    current = getattr(action, "body" if field == "text" else field, "")
    name = "email body" if field == "text" else FIELD_NAMES[field]
    tag = "pre" if field == "text" else "code"
    return f"Send the new {name}, or /cancel. Current:\n<{tag}>{text(current)}</{tag}>"

def version_preview(action: PlannedAction) -> str:
    if isinstance(action, PostSocial):
        return action.text
    return f"To: {action.to}\nSubject: {action.subject}\n\n{action.body}"

def your_version_text(persona: Persona, action: PlannedAction, changes: list[str]) -> str:
    preview = clip(version_preview(action), PREVIEW_LIMIT)
    tag = "blockquote expandable" if len(preview) > EXPANDABLE_AFTER else "blockquote"
    count = len(action.images)
    pictures = f" · {count} image{'s' if count != 1 else ''}" if count or changes else ""
    meta = "✎ " + " · ".join(changes) + pictures if changes else "No changes yet" + pictures
    return (
        f"{header(persona)}Your version: <b>{text(action_title(action))}</b>\n"
        f"<{tag}>{text(preview)}</blockquote>\n<i>{text(meta)}</i>"
    )

def your_version_keyboard(approval_id: str) -> Keyboard:
    return [
        [Button("✓ Approve my version", f"{EDIT_APPROVE}:{approval_id}")],
        [
            Button("✎ Change more", f"{EDIT_MORE}:{approval_id}"),
            Button("↺ Undo my changes", f"{EDIT_UNDO}:{approval_id}"),
        ],
    ]

BAD_ADDRESS = "That doesn't look like an email address. Send it again, or /cancel."
EMPTY_FIELD = "That's empty. Send it again, or /cancel."

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

TOPIC_ICONS = {"marketing": "📣", "design": "🎨", "finance": "💰", "sales": "🤝"}

def roster_text(event: TeamHired) -> str:
    lines = [f"{text(p.name)} · {text(p.role)}" for p in event.personas]
    return f"<b>Your {text(event.display_name)} team</b>\n" + "\n".join(lines)

def hired_text(event: TeamHired) -> str:
    names = join_names([p.name for p in event.personas])
    verb = "is" if len(event.personas) == 1 else "are"
    name = text(event.display_name)
    return f"<b>{name} team hired.</b> {text(names)} {verb} waiting in the {name} topic."

def message_link(chat_id: int, thread_id: int | None, message_id: int) -> str:
    base = f"https://t.me/c/{str(chat_id).removeprefix('-100')}"
    return f"{base}/{thread_id}/{message_id}" if thread_id else f"{base}/{message_id}"

def topic_link(chat_id: int, thread_id: int) -> str:
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{thread_id}"

def open_topic_keyboard(name: str, url: str) -> Keyboard:
    return [[Button(f"→ Open {name}", url=url)]]

def hire_keyboard(templates: list[TemplateInfo]) -> Keyboard:
    buttons = [Button(t.display_name, f"{HIRE}:{t.name}") for t in templates]
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

def hiring(name: str) -> str:
    return f"→ Hiring {name}"

def replace_text(template: TemplateInfo) -> str:
    people = join_names([p.name for p in template.personas])
    return (
        f"You already have a <b>{text(template.display_name)}</b> team ({text(people)}). "
        "Replace it?\n\nThe old team's topic, drafts and what it learned about you will be "
        "deleted. Business-wide lessons and the activity log stay."
    )

def replace_keyboard(key: str) -> Keyboard:
    return [[Button("Replace team", f"{REPLACE}:{key}"), Button("Cancel", f"{KEEP}:{key}")]]

def replacing(name: str) -> str:
    return f"→ Replacing {name}"

def kept(name: str) -> str:
    return f"Kept your current {name} team."

def unknown_team(name: str) -> str:
    return f"I don't know a team called “{text(name)}”. Which team do you want to hire?"

def queued(name: str) -> str:
    return f"<i>⏳ Queued: {text(name)} gets to this after the current draft.</i>"

def stopped(name: str) -> str:
    return f"Stopped {text(name)}'s current work. Nothing was sent."

def topic_reopened(icon: str | None, name: str) -> str:
    label = f"{icon} {name}" if icon else name
    return (
        f"The {text(label)} topic was deleted, so I opened a new one. "
        f"{text(name)}'s messages go there now."
    )

def split_text(value: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """
    Splits a long message at line breaks (or spaces) into parts Telegram accepts. HTML tags
    only appear in the header line, so the cuts never land inside one.
    """
    parts = []
    while len(value) > limit:
        cut = value.rfind("\n", 0, limit)
        if cut <= 0:
            cut = value.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(value[:cut])
        value = value[cut:].lstrip("\n ")
    return [*parts, value]

DASHBOARD_STOPPED = (
    "Dashboard turned off. The old link and password no longer work. "
    "Send /dashboard to turn it on with a new link."
)

def dashboard_text(password: str, minutes: int, url: str | None = None) -> str:
    """
    With url, the link is written out because Telegram refused the link button (local URLs).
    """
    link = f"\n{text(url)}" if url else ""
    steps = "open the link above" if url else "<b>Open dashboard</b>"
    return (
        f"<b>Your dashboard</b>{link}\nPassword: <code>{text(password)}</code>\n\n"
        f"Tap <b>Copy password</b>, then {steps} and paste it.\n\n"
        "<i>Keep both private. Sending /dashboard again makes a new password and signs out "
        f"other browsers. This message deletes itself in {minutes} minutes.</i>"
    )

def dashboard_keyboard(password: str, url: str | None = None) -> Keyboard:
    copy = Button("Copy password", copy=password)
    return [[copy, Button("→ Open dashboard", url=url)]] if url else [[copy]]

def too_big(size: int, limit: int) -> str:
    return (
        f"That file is {round(size / 1_048_576)} MB; I can take files up to "
        f"{limit // 1_048_576} MB. Send a smaller one, or tell me what's in it."
    )

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SCHEDULE_STOPPED = "Stopped"
TEAM_BUSY = "The team is waiting for your answer first. Answer it, then try again."

def _place(timezone: str) -> str:
    return timezone.rsplit("/", 1)[-1].replace("_", " ")

def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

def cadence_words(schedule: Schedule) -> str:
    cadence = schedule.cadence
    at = f"{cadence.hour}:{cadence.minute:02d} ({_place(cadence.timezone)})"
    if cadence.every == "day":
        return f"Every day at {at}"
    if cadence.every == "month":
        return f"Every month on the {_ordinal(cadence.day or 1)} at {at}"
    if cadence.weekday is None:
        return f"Every week at {at}"
    return f"Every {WEEKDAY_NAMES[cadence.weekday]} at {at}"

def when(moment: datetime, timezone: str) -> str:
    local = moment.astimezone(ZoneInfo(timezone))
    return f"{local:%a %b} {local.day}, {local.hour}:{local.minute:02d}"

def schedule_text(
    persona: Persona, schedule: Schedule, running: bool = False, queued: bool = False
) -> str:
    title = text(schedule.title)
    if not schedule.active:
        return f"{header(persona)}<s>🔁 {title}</s>\n<i>{SCHEDULE_STOPPED}</i>"
    upcoming = "being scheduled"
    if schedule.next_run_at is not None:
        upcoming = when(schedule.next_run_at, schedule.cadence.timezone)
    if queued:
        status = f"{QUEUED_RUN} · next: {upcoming}"
    elif running:
        status = f"Running now · next: {upcoming}"
    else:
        status = f"Next: {upcoming} · drafts wait for your OK"
    return (
        f"{header(persona)}🔁 <b>{title}</b>\n{text(cadence_words(schedule))}\n"
        f"<i>{text(status)}</i>"
    )

def schedule_keyboard(schedule: Schedule, running: bool = False) -> Keyboard:
    key = schedule.schedule_id
    if not schedule.active:
        return [[Button("↻ Turn back on", f"{TURN_ON}:{key}")]]
    stop = Button("■ Stop", f"{STOP}:{key}")
    return [[stop]] if running else [[Button("▶ Run now", f"{RUN_NOW}:{key}"), stop]]

def error_text(event: Error) -> str:
    body = f"⚠️ {text(event.message)}"
    if not event.recoverable:
        body += "\nUse /help if this keeps happening."
    return body
