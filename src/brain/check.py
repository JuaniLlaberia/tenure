import base64
import logging

from pydantic import BaseModel

from brain.deps import Deps
from brain.helpers.decide import decide
from brain.prompts.check import PASSES_CHECK, review_text
from brain.prompts.critic import MAX_ISSUES, CriticReport, critic_messages
from brain.templates.models import TaskTypeSpec, Template
from brain.templates.registries import OUTPUTS
from brain.tools import is_plan
from contract import BusinessProfile, FileRef, Lesson

log = logging.getLogger(__name__)

class CheckResult(BaseModel):
    passed: bool
    confidence: float
    feedback: list[str] = []
    tokens: int = 0

def hard_validate(
    output_type: str, output: BaseModel, media: dict[str, FileRef] | None = None
) -> list[str]:
    return OUTPUTS[output_type].validate(output, media)

async def run_check(
    deps: Deps,
    template: Template,
    task_type: str,
    output: BaseModel,
    lessons: list[Lesson],
    profile: BusinessProfile | None = None,
    asked: str = "",
    media: dict[str, FileRef] | None = None,
    prompts: dict[str, str] | None = None,
) -> CheckResult:
    """
    Independent review of a finished draft: hard validators, then pass / revise through
    decide(), then a critic that writes the feedback only when the answer is revise.
    Images ride along as text for Jev (alt text and prompt); the critic sees the images.
    """
    spec = template.task_types[task_type]
    errors = hard_validate(spec.output, output, media)
    if errors:
        return CheckResult(passed=False, confidence=0.0, feedback=errors)

    attached = OUTPUTS[spec.output].images(output, media)
    draft = OUTPUTS[spec.output].preview(output) + photos_line(attached, prompts or {})
    review = review_text(task_type, spec, lessons, draft, profile, asked)
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

    feedback, critic_tokens = await _critic(deps, spec, review, attached)
    return CheckResult(
        passed=False,
        confidence=confidence,
        feedback=feedback,
        tokens=decisions.tokens + critic_tokens,
    )

async def _critic(
    deps: Deps, spec: TaskTypeSpec, review: str, attached: list[FileRef]
) -> tuple[list[str], int]:
    """
    The critic model chooses how much to think: Sonnet can't turn reasoning off and barely
    uses it here.
    """
    images = await _image_urls(deps, attached)
    try:
        result = await deps.llm.structured(
            deps.settings.model_critic, critic_messages(review, images), CriticReport
        )
    except Exception as error:
        log.warning("Critic failed, falling back to the rules: %s", error)
        return _fallback(spec), 0
    issues = [issue.strip() for issue in result.value.issues if issue.strip()][:MAX_ISSUES]
    return issues or _fallback(spec), result.tokens

async def _image_urls(deps: Deps, attached: list[FileRef]) -> list[str]:
    urls = []
    for file in attached:
        if is_plan(file):
            continue
        try:
            data = await deps.tools.read_file(file.business_id, file.file_id)
        except Exception as error:
            log.warning("Couldn't read image %s for the critic: %s", file.file_id, error)
            data = None
        if data:
            urls.append(f"data:{file.mime_type};base64,{base64.b64encode(data).decode()}")
    return urls

def photos_line(attached: list[FileRef], prompts: dict[str, str] | None = None) -> str:
    """
    Jev can't see images, so the draft says which ones go with it: the founder's photos by
    their description, made images by their alt text and prompt.
    """
    if not attached:
        return ""
    founder = all(file.source == "founder" for file in attached)
    noun = "photo" if founder else "image"
    noun += "" if len(attached) == 1 else "s"
    prompts = prompts or {}
    shown = []
    for file in attached:
        text = file.alt_text or f"a {noun.rstrip('s')}"
        if file.file_id in prompts:
            text += f" (prompt: {prompts[file.file_id]})"
        shown.append(text)
    return f"\n\nWith {len(attached)} {noun}: {'; '.join(shown)}"

def _fallback(spec: TaskTypeSpec) -> list[str]:
    if not spec.check:
        return ["Make sure the draft follows the founder's lessons."]
    return ["Make sure the draft follows: " + "; ".join(spec.check)]
