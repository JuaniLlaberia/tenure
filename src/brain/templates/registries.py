import re
from collections.abc import Callable

from pydantic import BaseModel

from contract import PlannedAction, PostSocial, SendEmail

POST_LIMIT = 300

PLACEHOLDERS = [
    re.compile(r"\[[A-Z][^\]\n]{0,40}\](?!\()"),
    re.compile(r"\{\{[^}\n]*\}\}"),
    re.compile(r"<[A-Z][A-Za-z ]{0,30}>"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
]

Validator = Callable[[BaseModel], list[str]]

class SocialPostOutput(BaseModel):
    text: str

class EmailOutput(BaseModel):
    to: str
    subject: str
    body: str

class ReportOutput(BaseModel):
    summary: str
    sources: list[str]

class NotesOutput(BaseModel):
    notes: str
    sources: list[str]

class OutputType:
    def __init__(
        self,
        name: str,
        schema: type[BaseModel],
        render: Callable[[BaseModel], str],
        actions: dict[str, Callable[[BaseModel], PlannedAction]] | None = None,
        validate: Validator | None = None,
    ):
        self.name = name
        self.schema = schema
        self._render = render
        self._actions = actions or {}
        self._validate = validate

    def preview(self, output: BaseModel) -> str:
        return self._render(output)

    def validate(self, output: BaseModel) -> list[str]:
        """
        Hard checks in code: this output type's own rules, then placeholder text in any field.
        """
        errors = self._validate(output) if self._validate else []
        return errors + _placeholder_errors(output)

    def to_planned_action(self, output: BaseModel, action: str | None) -> PlannedAction | None:
        if action is None:
            return None
        if action not in self._actions:
            raise ValueError(f"Output {self.name!r} can't be used for action {action!r}")
        return self._actions[action](output)

def _render_email(output: EmailOutput) -> str:
    return f"To: {output.to}\nSubject: {output.subject}\n\n{output.body}"

def _render_with_sources(text: str, sources: list[str]) -> str:
    if not sources:
        return text
    return text + "\n\nSources:\n" + "\n".join(f"- {source}" for source in sources)

def _texts(output: BaseModel) -> list[str]:
    texts = []
    for value in output.model_dump().values():
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            texts.extend(item for item in value if isinstance(item, str))
    return texts

def _placeholder_errors(output: BaseModel) -> list[str]:
    found = []
    for text in _texts(output):
        for pattern in PLACEHOLDERS:
            found.extend(match.group(0) for match in pattern.finditer(text))
    return [f"Placeholder text left in the draft: {item}" for item in dict.fromkeys(found)]

def _validate_post(output: SocialPostOutput) -> list[str]:
    if not output.text.strip():
        return ["The post is empty."]
    if len(output.text) > POST_LIMIT:
        return [f"The post is {len(output.text)} characters; Bluesky allows {POST_LIMIT}."]
    return []

def _validate_email(output: EmailOutput) -> list[str]:
    errors = []
    if "@" not in output.to:
        errors.append(f"The recipient {output.to!r} is not an email address or list.")
    if not output.subject.strip():
        errors.append("The email has no subject line.")
    if not output.body.strip():
        errors.append("The email body is empty.")
    return errors

def _validate_report(output: ReportOutput) -> list[str]:
    errors = []
    if not output.summary.strip():
        errors.append("The report summary is empty.")
    if not output.sources:
        errors.append("The report lists no sources.")
    return errors

def _validate_notes(output: NotesOutput) -> list[str]:
    return [] if output.notes.strip() else ["The notes are empty."]

OUTPUTS: dict[str, OutputType] = {
    "social_post": OutputType(
        "social_post",
        SocialPostOutput,
        render=lambda output: output.text,
        actions={"post_social": lambda output: PostSocial(text=output.text)},
        validate=_validate_post,
    ),
    "email": OutputType(
        "email",
        EmailOutput,
        render=_render_email,
        actions={
            "send_email": lambda output: SendEmail(
                to=output.to, subject=output.subject, body=output.body
            )
        },
        validate=_validate_email,
    ),
    "report": OutputType(
        "report",
        ReportOutput,
        render=lambda output: _render_with_sources(output.summary, output.sources),
        validate=_validate_report,
    ),
    "notes": OutputType(
        "notes",
        NotesOutput,
        render=lambda output: _render_with_sources(output.notes, output.sources),
        validate=_validate_notes,
    ),
}

ACTIONS: dict[str, str] = {"post_social": "social_post", "send_email": "email"}

TOOL_NAMES: frozenset[str] = frozenset({"read_memory", "web_search", "fetch_page"})
