from pydantic import BaseModel

from brain.helpers.decide import Question
from brain.helpers.llm import Message
from brain.templates.models import TaskTypeSpec, Template
from contract import Task, TaskStatus

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

NAMES_CHANNELS = Question.yes_no(
    "Does the founder's message say where it should go out: a channel or a format like a "
    "Bluesky or social post, a newsletter or an email?",
    yes="Yes: it names at least one channel or format",
    no="No: it asks for a campaign or promotion without saying where",
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

def reply_messages(template: Template, request: str, context: str, no_match: bool) -> list[Message]:
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
    )
    system = _lead_system(template) + f"\n\n{task}"
    user = request if not context else f"{request}\n\n{context}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def report_text(tasks: list[Task]) -> str | None:
    waiting = [t.title for t in tasks if t.status == TaskStatus.WAITING_APPROVAL]
    done = [t.title for t in tasks if t.status == TaskStatus.DONE]
    if not waiting and not done:
        return None
    lines = [f"- {title}: ready for your approval" for title in waiting]
    lines += [f"- {title}: done" for title in done]
    return "Here's where we are:\n" + "\n".join(lines)
