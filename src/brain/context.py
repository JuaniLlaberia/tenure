from brain.deps import Deps
from contract import Approval, BusinessProfile, Lesson

MAX_LESSONS = 20
MAX_EXAMPLES = 3
MAX_FETCH_CHARS = 8_000

async def build_context(
    deps: Deps,
    business_id: str,
    team_id: str | None,
    task_type: str | None,
    prior_outputs: dict[str, dict] | None = None,
    feedback: list[str] | None = None,
) -> str:
    """
    The prompt context every role sees: business profile, lessons, approved examples,
    earlier steps' outputs and feedback to fix. Empty sections are left out.
    """
    profile = await deps.store.get_profile(business_id)
    lessons = await deps.store.list_lessons(business_id, team_id, task_type)
    examples: list[Approval] = []
    if team_id and task_type:
        examples = await deps.store.recent_approvals(team_id, task_type, limit=10)
    sections = [
        profile_section(profile),
        lessons_section(lessons),
        examples_section(examples),
        prior_section(prior_outputs or {}),
        feedback_section(feedback or []),
    ]
    return "\n\n".join(section for section in sections if section)

def profile_section(profile: BusinessProfile | None) -> str:
    if profile is None:
        return ""
    lines = [
        f"Name: {profile.name}",
        f"Sells: {profile.what_you_sell}",
        f"Customers: {profile.customers}",
    ]
    if profile.prices:
        lines.append(f"Prices: {profile.prices}")
    if profile.tone:
        lines.append(f"Tone: {profile.tone}")
    if profile.main_clients:
        lines.append(f"Main clients: {', '.join(profile.main_clients)}")
    if profile.links:
        lines.append(f"Links: {', '.join(profile.links)}")
    lines.extend(f"{key}: {value}" for key, value in profile.extra.items())
    return "Business:\n" + "\n".join(lines)

def lessons_section(lessons: list[Lesson]) -> str:
    if not lessons:
        return ""
    newest = sorted(lessons, key=lambda lesson: lesson.created_at, reverse=True)[:MAX_LESSONS]
    return "What you know about this business (follow it):\n" + "\n".join(
        f"- {lesson.text}" for lesson in newest
    )

def examples_section(approvals: list[Approval]) -> str:
    liked = [a for a in approvals if a.status in ("approved", "edited")][:MAX_EXAMPLES]
    if not liked:
        return ""
    drafts = [a.edited_text if a.status == "edited" and a.edited_text else a.preview for a in liked]
    return (
        "Drafts the founder approved (match their style only; their facts and dates may be "
        "out of date):\n" + "\n---\n".join(drafts)
    )

def prior_section(prior_outputs: dict[str, dict]) -> str:
    if not prior_outputs:
        return ""
    parts = []
    for specialist_id, output in prior_outputs.items():
        lines = []
        for key, value in output.items():
            if isinstance(value, list):
                value = "\n".join(f"  - {item}" for item in value) or "  (none)"
                lines.append(f"{key}:\n{value}")
            else:
                lines.append(f"{key}: {value}")
        parts.append(f"[{specialist_id}]\n" + "\n".join(lines))
    return "Earlier steps of this task:\n" + "\n\n".join(parts)

def feedback_section(feedback: list[str]) -> str:
    if not feedback:
        return ""
    return "Fix these, staying on your brief:\n" + "\n".join(f"- {item}" for item in feedback)
