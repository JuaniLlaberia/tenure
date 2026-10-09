from pydantic import BaseModel

MAX_ISSUES = 3

class CriticReport(BaseModel):
    issues: list[str]

SYSTEM = (
    "You review a draft written for a small business before the founder sees it. "
    "List only real problems: a broken rule or lesson, an invented fact, a missing requirement. "
    "Facts in what the founder asked for are given, not invented. Judge the draft against that "
    "request and never suggest a different topic. "
    f"Give at most {MAX_ISSUES} issues, most important first, each one short sentence (under 20 "
    "words) naming the problem and the fix. Don't rewrite the draft or explain. "
    'Reply as JSON: {"issues": ["..."]}.'
)

def critic_messages(review: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": review}]
