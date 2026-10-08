from pydantic import BaseModel

MAX_ISSUES = 5

class CriticReport(BaseModel):
    issues: list[str]

SYSTEM = (
    "You review a draft written for a small business before the founder sees it. "
    "List each concrete problem: which rule or lesson it breaks, and what to change. "
    f"Give at most {MAX_ISSUES} short issues. Don't rewrite the draft. "
    'Reply as JSON: {"issues": ["..."]}.'
)

def critic_messages(review: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": review}]
