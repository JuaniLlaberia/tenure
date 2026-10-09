import logging
from typing import Literal

from pydantic import BaseModel, field_validator

from brain.deps import Deps
from brain.helpers.jev import JevAnswer

log = logging.getLogger(__name__)

MAX_STATE_CHARS = 60_000

class Question(BaseModel):
    instructions: str
    options: dict[str, str]

    @field_validator("options")
    @classmethod
    def _at_least_two(cls, options: dict[str, str]) -> dict[str, str]:
        if len(options) < 2:
            raise ValueError("A question needs at least two options")
        return options

    @classmethod
    def yes_no(cls, instructions: str, yes: str, no: str) -> "Question":
        return cls(instructions=instructions, options={"yes": yes, "no": no})

    @property
    def safe(self) -> str:
        return list(self.options)[-1]

class Decision(BaseModel):
    choice: str
    confidence: float
    source: Literal["jev", "llm", "default"]

    def accepts(self, option: str, threshold: float) -> bool:
        return self.choice == option and self.confidence >= threshold

class Decisions(BaseModel):
    answers: dict[str, Decision]
    tokens: int = 0

    def __getitem__(self, key: str) -> Decision:
        return self.answers[key]

class FallbackAnswer(BaseModel):
    choice: str
    confidence: float

class DecideFallback(BaseModel):
    answers: dict[str, FallbackAnswer]

async def decide(deps: Deps, questions: dict[str, Question], state: str) -> Decisions:
    """
    Answers every question in one Jev call. Questions Jev can't answer go to one LLM call;
    anything still unanswered gets its safe option with confidence 0. Never raises.
    """
    state = _truncate(state)
    answers, tokens = await _ask_jev(deps, questions, state)
    missing = {key: q for key, q in questions.items() if key not in answers}
    if missing:
        fallback, fallback_tokens = await _ask_llm(deps, missing, state)
        answers.update(fallback)
        tokens += fallback_tokens
    for key, question in questions.items():
        answers.setdefault(key, Decision(choice=question.safe, confidence=0.0, source="default"))
    return Decisions(answers={key: answers[key] for key in questions}, tokens=tokens)

def _truncate(state: str) -> str:
    if len(state) <= MAX_STATE_CHARS:
        return state
    return "…" + state[-(MAX_STATE_CHARS - 1) :]

def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))

def _to_jev(question: Question) -> dict:
    if len(question.options) == 2:
        true_meaning, false_meaning = question.options.values()
        return {
            "type": "noul",
            "instructions": question.instructions,
            "criteria": {"true": true_meaning, "false": false_meaning},
        }
    return {"type": "choice", "instructions": question.instructions, "criteria": question.options}

def _from_jev(question: Question, answer: JevAnswer | None) -> Decision | None:
    if answer is None:
        return None
    options = list(question.options)
    if len(options) == 2:
        if answer.noul is None:
            return None
        p = _clamp(answer.noul)
        choice = options[0] if p >= 0.5 else options[1]
        return Decision(choice=choice, confidence=max(p, 1 - p), source="jev")
    if answer.choice not in question.options:
        return None
    confidence = answer.confidence
    if confidence is None:
        confidence = max((answer.probabilities or {}).values(), default=0.0)
    return Decision(choice=answer.choice, confidence=_clamp(confidence), source="jev")

async def _ask_jev(
    deps: Deps, questions: dict[str, Question], state: str
) -> tuple[dict[str, Decision], int]:
    payload = {key: _to_jev(question) for key, question in questions.items()}
    try:
        response = await deps.jev.ask(deps.settings.model_jev, state, payload)
    except Exception as error:
        log.warning("Jev failed, using the LLM fallback: %s", error)
        return {}, 0
    answers = {}
    for key, question in questions.items():
        decision = _from_jev(question, response.answers.get(key))
        if decision is not None:
            answers[key] = decision
    return answers, response.input_tokens + response.output_tokens

async def _ask_llm(
    deps: Deps, questions: dict[str, Question], state: str
) -> tuple[dict[str, Decision], int]:
    try:
        messages = _fallback_messages(questions, state)
        result = await deps.llm.structured(
            deps.settings.model_decide_fallback, messages, DecideFallback, reasoning=False
        )
    except Exception as error:
        log.warning("Decide fallback failed, using safe options: %s", error)
        return {}, 0
    answers = {}
    for key, question in questions.items():
        answer = result.value.answers.get(key)
        if answer is not None and answer.choice in question.options:
            answers[key] = Decision(
                choice=answer.choice, confidence=_clamp(answer.confidence), source="llm"
            )
    return answers, result.tokens

def _fallback_messages(questions: dict[str, Question], state: str) -> list[dict]:
    lines = []
    for key, question in questions.items():
        options = "\n".join(
            f'    - "{option}": {meaning}' for option, meaning in question.options.items()
        )
        lines.append(f'- "{key}": {question.instructions}\n  Options:\n{options}')
    system = (
        "You make quick decisions about the state below. For each question, pick exactly one "
        "option key and give your confidence from 0 to 1. Reply as JSON: "
        '{"answers": {"<question key>": {"choice": "<option key>", "confidence": 0.0}}}'
    )
    user = f"State:\n{state}\n\nQuestions:\n" + "\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
