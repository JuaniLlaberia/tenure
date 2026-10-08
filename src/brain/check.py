import logging

from pydantic import BaseModel

from brain.deps import Deps
from brain.helpers.decide import decide
from brain.prompts.check import PASSES_CHECK, review_text
from brain.prompts.critic import MAX_ISSUES, CriticReport, critic_messages
from brain.templates.models import TaskTypeSpec, Template
from brain.templates.registries import OUTPUTS
from contract import Lesson

log = logging.getLogger(__name__)

class CheckResult(BaseModel):
    passed: bool
    confidence: float
    feedback: list[str] = []
    tokens: int = 0

def hard_validate(output_type: str, output: BaseModel) -> list[str]:
    return OUTPUTS[output_type].validate(output)

async def run_check(
    deps: Deps, template: Template, task_type: str, output: BaseModel, lessons: list[Lesson]
) -> CheckResult:
    """
    Independent review of a finished draft: hard validators, then pass / revise through
    decide(), then a critic that writes the feedback only when the answer is revise.
    """
    spec = template.task_types[task_type]
    errors = hard_validate(spec.output, output)
    if errors:
        return CheckResult(passed=False, confidence=0.0, feedback=errors)

    review = review_text(task_type, spec, lessons, OUTPUTS[spec.output].preview(output))
    decisions = await decide(deps, {"passes_check": PASSES_CHECK}, review)
    decision = decisions["passes_check"]
    if decision.source == "default":
        confidence = 0.0
    elif decision.choice == "pass":
        confidence = decision.confidence
    else:
        confidence = 1 - decision.confidence
    if decision.accepts("pass", deps.settings.decide_threshold):
        return CheckResult(passed=True, confidence=confidence, tokens=decisions.tokens)

    feedback, critic_tokens = await _critic(deps, spec, review)
    return CheckResult(
        passed=False,
        confidence=confidence,
        feedback=feedback,
        tokens=decisions.tokens + critic_tokens,
    )

async def _critic(deps: Deps, spec: TaskTypeSpec, review: str) -> tuple[list[str], int]:
    try:
        result = await deps.llm.structured(
            deps.settings.model_reflect, critic_messages(review), CriticReport
        )
    except Exception as error:
        log.warning("Critic failed, falling back to the rules: %s", error)
        return _fallback(spec), 0
    issues = [issue.strip() for issue in result.value.issues if issue.strip()][:MAX_ISSUES]
    return issues or _fallback(spec), result.tokens

def _fallback(spec: TaskTypeSpec) -> list[str]:
    if not spec.check:
        return ["Make sure the draft follows the founder's lessons."]
    return ["Make sure the draft follows: " + "; ".join(spec.check)]
