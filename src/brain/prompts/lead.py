from typing import Literal

from pydantic import BaseModel

from brain.helpers.decide import Question
from brain.helpers.llm import Message
from brain.templates.models import TaskTypeSpec, Template
from contract import Cadence, Schedule, Task, TaskStatus

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

class TaskPlan(BaseModel):
    task_type: str
    title: str
    brief: str

class LeadPlan(BaseModel):
    tasks: list[TaskPlan]
    question: str | None = None

HAS_FEEDBACK = Question.yes_no(
    "Does the founder's message give feedback, a correction or a preference about how the team "
    "should work (tone, style, what to avoid, facts about the business)?",
    yes="It contains feedback or a preference",
    no="No feedback; it's only a request, a question or small talk",
)

ONE_OFF = Question.yes_no(
    "Is any feedback in the founder's message only about this one piece of work (a date, a "
    "topic, an offer or a detail for this request), rather than something the team should keep "
    "doing from now on?",
    yes="Only about this request; nothing to remember for later",
    no="Something the team should remember, or there's no feedback",
)

NAMES_CHANNELS = Question.yes_no(
    "Does the founder's message say where it should go out: a channel or a format like a "
    "Bluesky or social post, a newsletter or an email?",
    yes="Yes: it names at least one channel or format",
    no="No: it asks for a campaign or promotion without saying where",
)

MENTIONS_IMAGE = Question.yes_no(
    "Does the founder's message say whether this work should come with an image, picture or "
    "graphic (either that they want one or that they don't)?",
    yes="Yes: it says whether to make an image",
    no="No: images aren't mentioned",
)

WANTS_IMAGE = Question.yes_no(
    "Does the founder want an image, picture or graphic made for this work?",
    yes="Yes: make an image",
    no="No image, or it isn't clear",
)

CANCELS = Question.yes_no(
    "The team asked the founder a question about their request. Does the founder's reply drop "
    "the request (never mind, forget it, cancel, not now)?",
    yes="They want to drop the request",
    no="They answer the question, or say something else",
)

NEW_REQUEST = Question.yes_no(
    "The team asked the founder a question about their request. Is the founder's reply a new, "
    "unrelated request instead of an answer?",
    yes="A new request that doesn't answer the question",
    no="It answers the question, even briefly (like 'yes' or a channel name)",
)

IS_DRAFT_FEEDBACK = Question.yes_no(
    "The team sent the founder a draft to approve. Is the founder's new message a change they "
    "want to that draft (shorter, another tone, fix a fact, add or remove something), rather "
    "than a new request, a question or something else?",
    yes="A change to the draft",
    no="A new request or something else",
)

OTHER_CHANNEL = Question.yes_no(
    "Does the founder want something made for or posted on a channel other than Bluesky or "
    "email, like Instagram, LinkedIn, TikTok, X (Twitter), Facebook or YouTube?",
    yes="Yes: they name another channel for this work",
    no="No: Bluesky, email, or no channel named",
)

WANTS_BLUESKY = Question.yes_no(
    "The team offered to make a Bluesky post instead. Does the founder accept?",
    yes="Yes, a Bluesky post",
    no="No, or it isn't clear",
)

OTHER_CHANNEL_ASK = (
    "I can only post to Bluesky and send emails for now. Want this as a Bluesky post instead?"
)
OTHER_CHANNEL_REPLIES = ["Yes, a Bluesky post", "No"]

DROPPED = "Okay, I've dropped that request."
SET_ASIDE = "I'll set aside my question about {topic} and start on this."

IMAGE_QUESTION = "Want {name} to make an image for this?"
NO_IMAGE_MADE = (
    "{name} couldn't make the image this time, so this draft has none. Reject it with a note "
    "about the image to try again."
)
IMAGE_REPLIES = ["Yes, make an image", "No image"]

class ScheduleDraft(BaseModel):
    """
    A schedule as the model read it. Anything not said is null; code fills the defaults.
    """

    title: str | None = None
    request: str | None = None
    every: Literal["day", "week", "month"] | None = None
    weekday: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    timezone: str | None = None

WANTS_SCHEDULE = Question.yes_no(
    "Is the founder asking for something to happen repeatedly, on a schedule (every day, each "
    "Monday, monthly)?",
    yes="Yes: they want it to happen again and again",
    no="No: it's a one-off request, a question or small talk",
)

STOPS_SCHEDULE = Question.yes_no(
    "Is the founder asking to stop or pause the schedule, rather than change when it runs or "
    "what it does?",
    yes="Stop or pause it",
    no="Change it",
)

def changes_question(schedules: list[Schedule]) -> Question:
    titles = "; ".join(f"“{schedule.title}”" for schedule in schedules)
    return Question.yes_no(
        f"Is the founder asking to stop, pause or change one of these schedules: {titles}?",
        yes="Yes: stop, pause or change a schedule",
        no="No: something else",
    )

def which_question(schedules: list[Schedule]) -> Question:
    options = {
        f"s{n}": f"{schedule.title} ({cadence_words(schedule.cadence)})"
        for n, schedule in enumerate(schedules, start=1)
    }
    return Question(
        instructions="Which of these schedules is the founder talking about?",
        options={**options, "none": "None of them, or it isn't clear"},
    )

def cadence_words(cadence: Cadence) -> str:
    """
    "Every Monday at 9:00", the way the schedule card says it.
    """
    time = f"{cadence.hour}:{cadence.minute:02d}"
    if cadence.every == "week" and cadence.weekday is not None:
        return f"Every {WEEKDAYS[cadence.weekday]} at {time}"
    if cadence.every == "month":
        return f"On day {cadence.day or 1} of every month at {time}"
    return f"Every day at {time}"

def schedule_messages(
    template: Template, request: str, current: Schedule | None = None
) -> list[Message]:
    system = (
        _lead_system(template)
        + "\n\nThe founder wants something done again and again. Read their message as JSON "
        "with: title (a short label for the schedule card, under 40 characters), request (what "
        "to do each time, in the founder's words, without the repeat part: no \"every Monday\", "
        "\"each day\" or \"at 9\"), every (day, week or month), weekday (0 = Monday … 6 = "
        "Sunday, for weekly), day (1 to 28, for monthly), hour and minute (24-hour local time), "
        "timezone (an IANA name like Europe/Madrid, only if they name a place or zone). Use null "
        "for anything they didn't say."
    )
    if current is not None:
        system += (
            f" They are changing this schedule: “{current.title}”, "
            f"{cadence_words(current.cadence)}: {current.request}. Give only what they change "
            "and null for the rest."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"The founder's message:\n{request}"},
    ]

NAMES_SEND_TIME = Question.yes_no(
    "Does the founder say one specific future time for this to go out or be sent (\"this Friday "
    "at 6 PM\", \"tomorrow morning at 9\"), once, not repeating?",
    yes="Yes: one time to send it",
    no="No time, now, or a repeating time",
)

class SendTime(BaseModel):
    """
    When the founder wants the draft to go out, in their local time. Null when not said.
    """

    date: str | None = None
    time: str | None = None

def send_time_messages(request: str, today: str) -> list[Message]:
    system = (
        "Read when a solo founder wants their post or email to go out. Today is "
        f"{today} (their local time). Reply as JSON: {{\"date\": \"YYYY-MM-DD\", \"time\": "
        '"HH:MM"}, 24-hour local time. "Morning" is 09:00, "afternoon" 15:00, "evening" 18:00. '
        "Use null for anything they didn't say."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"The founder's message:\n{request}"},
    ]

ABOUT_IMAGE = Question.yes_no(
    "Is this feedback only about the image that goes with the draft (its look, colours, subject "
    "or style), and not about the text?",
    yes="Only about the image",
    no="About the text, or both",
)

IS_CLEAR = Question(
    instructions=(
        "Given the founder's request and the plan, is there enough information to do the work well "
        "without asking the founder anything?"
    ),
    options={
        "clear": "Clear enough; a good team would just start",
        "unclear": "Something essential is missing, like the date, the offer or the audience",
    },
)

def needs_question(spec: TaskTypeSpec) -> Question:
    return Question.yes_no(
        f"Is the founder asking the team to produce this: {spec.description}?",
        yes=(
            "Yes: they ask for it directly, or ask to announce, share or find out something "
            "it covers"
        ),
        no="No: it's a different deliverable, a thank-you, small talk or a question",
    )

def triage_state(template: Template, request: str) -> str:
    """
    What Jev sees when routing: who the team is and what it makes, then the founder's message.
    Measured on Oct 8: much sharper than the bare message.
    """
    makes = "\n".join(f"- {spec.description}" for spec in template.task_types.values())
    return (
        f"A {template.display_name.lower()} team works for a solo founder. It can make:\n{makes}"
        f"\n\nThe founder's message:\n{request}"
    )

def _lead_system(template: Template) -> str:
    lead = template.lead.persona
    abilities = "\n".join(
        f"- {task_type}: {spec.description}" for task_type, spec in template.task_types.items()
    )
    return (
        f"You are {lead.name}, the {lead.role} of a small business's {template.display_name} team. "
        f"{template.lead.instructions}\n\nWhat your team can do:\n{abilities}"
    )

def plan_messages(
    template: Template,
    routed: list[str],
    request: str,
    context: str,
    history: list[str] | None = None,
) -> list[Message]:
    wanted = "\n".join(
        f"- {task_type}: {template.task_types[task_type].description}" for task_type in routed
    )
    system = (
        _lead_system(template)
        + "\n\nPlan the work. Write exactly one task for each of these task types:\n"
        + wanted
        + "\n\nEach task has a short title for the dashboard and a brief for your specialists: "
        "what to make, for whom, the key facts and dates, and the angle. Only use facts from the "
        "request and the context. The task types above are already decided: never ask which "
        "pieces or channels to make. If a fact you can't do without is missing (like the date or "
        'the offer), give the one question you would ask the founder in "question"; otherwise '
        "leave it null. A short follow-up (like \"now a post\" or \"also email them\") continues "
        "the most recent request: keep its facts, dates and angle unless the founder changes "
        "them. Older approved drafts and onboarding answers may be about earlier launches; the "
        "request and recent requests win. "
        'Reply as JSON: {"tasks": [{"task_type", "title", "brief"}], "question": null}.'
    )
    user = f"Founder's request:\n{request}"
    if history:
        user += "\n\nRecent requests to this team, oldest first:\n" + "\n---\n".join(history)
    if context:
        user += f"\n\n{context}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

CANT_DELETE = (
    "I can't delete posts from here. Undo it within 10 minutes from /activity, or delete it in "
    "Bluesky."
)

def reply_messages(
    template: Template,
    request: str,
    context: str,
    no_match: bool,
    others: list[tuple[Template, bool]] | None = None,
) -> list[Message]:
    """
    `others` are the other teams the founder can hire, each with whether it's hired, so the
    lead can send work that isn't its team's to the right team.
    """
    if no_match:
        task = (
            "The request doesn't match anything your team does. Say so kindly in one or two "
            "sentences and say what you can do instead."
        )
    else:
        task = "Reply to the founder in one or two friendly sentences."
    task += (
        " Never write a draft, post or email in this reply: drafts only come from your team, "
        "through review. If they seem to want one, ask them to say what to make."
        " Never say you posted, sent, scheduled or deleted anything. If they ask you to delete "
        f'a post, say exactly: "{CANT_DELETE}"'
    )
    if others:
        task += "\n\n" + _others_section(others)
    system = _lead_system(template) + f"\n\n{task}"
    user = request if not context else f"{request}\n\n{context}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def _others_section(others: list[tuple[Template, bool]]) -> str:
    lines = []
    for other, hired in others:
        makes = "; ".join(spec.description for spec in other.task_types.values())
        name, lead = other.display_name, other.lead.persona.name
        then = f"ask {lead} in the {name} topic."
        if not hired:
            then = f"Hire them with /hire {other.name}, then {then}"
        else:
            then = then[0].upper() + then[1:]
        say = f"<What they asked for> is {name}'s work. {then}"
        lines.append(f'- {name} makes: {makes}. If the request is theirs, say: "{say}"')
    return (
        "Other teams of this business (send work that's theirs to them, in one sentence, with "
        "the thing asked for in plural or singular as it reads best):\n" + "\n".join(lines)
    )

def report_text(tasks: list[Task]) -> str | None:
    waiting = [t.title for t in tasks if t.status == TaskStatus.WAITING_APPROVAL]
    done = [t.title for t in tasks if t.status == TaskStatus.DONE]
    if not waiting and not done:
        return None
    lines = [f"- {title}: ready for your approval" for title in waiting]
    lines += [f"- {title}: done" for title in done]
    return "Here's where we are:\n" + "\n".join(lines)
