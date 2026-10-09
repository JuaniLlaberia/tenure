from pydantic import BaseModel

from brain.context import MAX_FETCH_CHARS, profile_section
from brain.helpers.decide import Question
from brain.helpers.llm import Message
from contract import BusinessProfile, PageContent, Persona, Team

class ProfileDraft(BaseModel):
    name: str | None = None
    what_you_sell: str | None = None
    customers: str | None = None
    prices: str | None = None
    tone: str | None = None
    main_clients: list[str] = []
    links: list[str] = []
    extra: dict[str, str] = {}

class Extraction(BaseModel):
    fields: ProfileDraft
    facts: list[str] = []

FIELDS = {
    "name": "the business's name",
    "what_you_sell": "what they sell",
    "customers": "who their customers are",
    "tone": "how they like to sound (tone of voice)",
}

ANSWER_CLEAR = Question(
    instructions="Does the founder's answer actually answer the question it replies to?",
    options={
        "clear": "It answers it, even briefly, or gives a website to read",
        "unclear": "It's vague, off-topic or contradictory",
    },
)

def _persona_line(persona: Persona) -> str:
    return (
        f"You are {persona.name}, the {persona.role} of a solo founder's business. "
        "You're warm and brief."
    )

def question_messages(
    persona: Persona,
    draft: ProfileDraft,
    missing: list[str],
    last_question: str | None,
    last_answer: str | None,
    unclear: bool,
) -> list[Message]:
    wanted = ", ".join(FIELDS[field] for field in missing) or "anything else worth knowing"
    known = draft.model_dump(exclude_defaults=True)
    if unclear:
        task = (
            f"The founder's last answer was unclear. You asked: {last_question!r}. They said: "
            f"{last_answer!r}. Ask one short, friendly follow-up so you get {wanted}."
        )
    else:
        task = f"Ask one short message with one or two questions to learn {wanted}."
    system = (
        f"{_persona_line(persona)} You're interviewing the founder once, so every team they hire "
        f"already knows the business. {task} Don't repeat what you already know. "
        "Reply with the message only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"What you know so far: {known or 'nothing yet'}"},
    ]

def fallback_question(missing: list[str]) -> str:
    wanted = [FIELDS[field] for field in missing] or ["anything else I should know"]
    return "Thanks! Could you tell me " + " and ".join(wanted[:2]) + "?"

def extract_messages(
    question: str, answer: str, pages: list[PageContent], draft: ProfileDraft
) -> list[Message]:
    system = (
        "Pull facts about a solo founder's business out of their answer and, if included, out "
        "of their website. Fill every field the answer or the website supports, not only the "
        "one the question asked about: name, what_you_sell, customers, prices (as stated, e.g. "
        '"packages from $2,500"), tone (how they sound or want to sound), main_clients (named '
        "clients), links, extra (short key: value pairs). Leave a field null or empty only when "
        'nothing supports it. Put any other useful fact about the business in "facts" as short '
        'sentences. Never invent anything. Reply as JSON: {"fields": {...}, "facts": [...]}.'
    )
    user = f"Question: {question}\nAnswer: {answer}"
    for page in pages:
        title = page.title or "no title"
        user += f"\n\nWebsite {page.url} ({title}):\n{page.text[:MAX_FETCH_CHARS]}"
    user += f"\n\nAlready known: {draft.model_dump(exclude_defaults=True) or 'nothing'}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def reply_messages(
    persona: Persona,
    profile: BusinessProfile,
    teams: list[Team],
    hireable: list[str],
    message: str,
) -> list[Message]:
    hired = ", ".join(team.display_name for team in teams) or "none yet"
    system = (
        f"{_persona_line(persona)} Answer the founder in one or two sentences. Teams hired: "
        f"{hired}. Teams they can hire: {', '.join(hireable)} (with /hire <name>). "
        "If they ask for work, point them to the right team's topic or to hiring one. "
        "Never write drafts yourself."
    )
    user = f"{profile_section(profile)}\n\nThe founder says:\n{message}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
