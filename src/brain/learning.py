import logging
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel

from brain.common import new_id
from brain.deps import Deps
from brain.prompts.reflect import MAX_LESSONS, reflect_messages
from brain.templates.models import Template
from contract import Event, Lesson, LessonLearned

log = logging.getLogger(__name__)

class LessonDraft(BaseModel):
    text: str
    kind: Literal["fact", "preference"]
    business_wide: bool
    task_type: str | None = None
    replaces: str | None = None

class ReflectOutput(BaseModel):
    lessons: list[LessonDraft]

async def reflect(
    deps: Deps,
    template: Template,
    feedback: str,
    existing: list[Lesson],
    task_type: str | None = None,
) -> tuple[list[LessonDraft], int]:
    """
    One LLM call that turns feedback into lesson drafts. Raises if the call fails.
    """
    result = await deps.llm.structured(
        deps.settings.model_reflect,
        reflect_messages(template, feedback, existing, task_type),
        ReflectOutput,
    )
    return result.value.lessons, result.tokens

async def learn(
    deps: Deps,
    *,
    business_id: str,
    team_id: str,
    template: Template,
    source: Literal["edit", "reject", "chat"],
    source_ref: str | None,
    feedback: str,
    task_type: str | None = None,
) -> AsyncIterator[Event]:
    """
    Reflects on one piece of feedback, saves the lessons (replacing the ones they repeat or
    contradict) and yields a LessonLearned for each. Learning never blocks the work: if
    reflect fails, nothing is saved and nothing is yielded.
    """
    existing = await deps.store.list_lessons(business_id, team_id)
    try:
        drafts, _ = await reflect(deps, template, feedback, existing, task_type)
    except Exception as error:
        log.warning("Reflect failed, learning nothing from this feedback: %s", error)
        return
    by_id = {lesson.lesson_id: lesson for lesson in existing}
    known = {lesson.text.strip().lower() for lesson in existing}
    for draft in _clean(drafts, template, by_id, known):
        if draft.replaces:
            await deps.store.save_lesson(by_id[draft.replaces].model_copy(update={"active": False}))
        lesson = Lesson(
            lesson_id=new_id(),
            business_id=business_id,
            team_id=None if draft.business_wide else team_id,
            task_type=draft.task_type,
            kind=draft.kind,
            text=draft.text,
            source=source,
            source_ref=source_ref,
            created_at=deps.clock(),
        )
        await deps.store.save_lesson(lesson)
        yield LessonLearned(
            lesson_id=lesson.lesson_id,
            team_id=team_id,
            persona=template.lead.persona,
            text=lesson.text,
            business_wide=draft.business_wide,
        )

def _clean(
    drafts: list[LessonDraft], template: Template, by_id: dict[str, Lesson], known: set[str]
) -> list[LessonDraft]:
    """
    Code checks what reflect returned: no empty or already-known lessons, at most two, known
    task types only, and `replaces` only when it names an existing lesson.
    """
    cleaned = []
    for draft in drafts:
        text = draft.text.strip()
        replaces = draft.replaces if draft.replaces in by_id else None
        if not text or (text.lower() in known and replaces is None):
            continue
        task_type = draft.task_type if draft.task_type in template.task_types else None
        cleaned.append(
            draft.model_copy(update={"text": text, "task_type": task_type, "replaces": replaces})
        )
        known.add(text.lower())
    return cleaned[:MAX_LESSONS]
