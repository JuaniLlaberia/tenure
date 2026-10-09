from brain.context import profile_section
from brain.helpers.decide import Question
from brain.templates.models import TaskTypeSpec
from contract import BusinessProfile, Lesson

PASSES_CHECK = Question(
    instructions=(
        "Does this draft follow every rule and every lesson from the founder listed above, "
        "without invented facts, prices or placeholders?"
    ),
    options={
        "pass": "It follows every rule and lesson and is ready for the founder",
        "revise": "It breaks at least one rule or lesson, or invents something",
    },
)

def review_text(
    task_type: str,
    spec: TaskTypeSpec,
    lessons: list[Lesson],
    draft: str,
    profile: BusinessProfile | None = None,
) -> str:
    """
    What an independent reviewer sees: the business profile (to judge tone and invented facts),
    the task type, its rules, the founder's lessons and the draft. Never the lead's or the
    specialist's instructions.
    """
    rules = "\n".join(f"- {rule}" for rule in spec.check) or "- (no extra rules)"
    learned = "\n".join(f"- {lesson.text}" for lesson in lessons) or "- (none yet)"
    business = profile_section(profile)
    return (
        (f"{business}\n\n" if business else "")
        + f"Task type: {task_type} ({spec.description})\n\n"
        f"Rules:\n{rules}\n\n"
        f"Lessons from the founder:\n{learned}\n\n"
        f"Draft:\n{draft}"
    )
