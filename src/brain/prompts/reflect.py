from brain.helpers.llm import Message
from brain.templates.models import Template
from contract import Lesson

MAX_LESSONS = 2

def reflect_messages(
    template: Template, feedback: str, existing: list[Lesson], task_type: str | None
) -> list[Message]:
    task_types = ", ".join(template.task_types)
    system = (
        f"You help an AI {template.display_name.lower()} team learn from its founder. "
        f"Turn the founder's feedback into at most {MAX_LESSONS} short lessons, or none if it "
        "teaches nothing new.\n\n"
        "Each lesson has:\n"
        '- "text": short and imperative, e.g. "No emojis in posts", "Sign posts as Juan"\n'
        '- "kind": "fact" (something true about the business) or "preference" (how work '
        "should look)\n"
        '- "business_wide": true only for facts or rules about the business itself that every '
        'team should know ("We never offer discounts"); false for how this team\'s work should '
        'look ("No hashtags")\n'
        f'- "task_type": one of {task_types} if it only applies to that kind of work, else null\n'
        '- "replaces": the id of an existing lesson it repeats or contradicts, else null\n\n'
        'Reply as JSON: {"lessons": [...]}.'
    )
    known = "\n".join(f"- [{lesson.lesson_id}] {lesson.text}" for lesson in existing)
    user = f"Founder's feedback:\n{feedback}\n\nExisting lessons:\n{known or '- (none)'}"
    if task_type:
        user += f"\n\nThe feedback is about a {task_type} draft."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
