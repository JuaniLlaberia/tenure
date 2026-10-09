from brain.deps import Deps
from brain.helpers.decide import Decisions, Question, decide

NEEDS_REASONING = Question.yes_no(
    "Does handling this well need careful, multi-step thinking: research, weighing facts or "
    "options, or a long or delicate piece of writing?",
    yes="Yes: it needs careful thinking",
    no="No: a quick, simple reply or a short routine draft is enough",
)

def wants_reasoning(deps: Deps, decisions: Decisions) -> bool:
    return decisions["needs_reasoning"].accepts("yes", deps.settings.decide_threshold)

async def needs_reasoning(deps: Deps, state: str) -> tuple[bool, int]:
    """
    One Jev call: should the model reason for this? Unsure or failed means no.
    """
    decisions = await decide(deps, {"needs_reasoning": NEEDS_REASONING}, state)
    return wants_reasoning(deps, decisions), decisions.tokens
