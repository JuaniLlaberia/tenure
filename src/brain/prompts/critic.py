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

def critic_messages(review: str, images: list[str] | None = None) -> list[dict]:
    """
    `images` are data URLs of the draft's images, shown to the critic after the review text.
    """
    if not images:
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": review}]
    parts = [{"type": "text", "text": review}]
    parts.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": parts}]
