import re
from collections.abc import Callable

from pydantic import BaseModel

from contract import FileKind, FileRef, PlannedAction, PostSocial, SendEmail

POST_LIMIT = 300
MAX_IMAGES = 4

PLACEHOLDERS = [
    re.compile(r"\[[A-Z][^\]\n]{0,40}\](?!\()"),
    re.compile(r"\{\{[^}\n]*\}\}"),
    re.compile(r"<[A-Z][A-Za-z ]{0,30}>"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
]

Validator = Callable[[BaseModel], list[str]]
ActionBuilder = Callable[[BaseModel, list[FileRef]], PlannedAction]

class SocialPostOutput(BaseModel):
    text: str
    images: list[str] = []

class EmailOutput(BaseModel):
    to: str
    subject: str
    body: str
    images: list[str] = []

class ReportOutput(BaseModel):
    summary: str
    sources: list[str]

class NotesOutput(BaseModel):
    notes: str
    sources: list[str]

class ImageOutput(BaseModel):
    images: list[str]
    caption: str

class OutputType:
    def __init__(
        self,
        name: str,
        schema: type[BaseModel],
        render: Callable[[BaseModel], str],
        actions: dict[str, ActionBuilder] | None = None,
        validate: Validator | None = None,
        guidance: str = "",
        min_images: int = 0,
    ):
        self.name = name
        self.schema = schema
        self._render = render
        self._actions = actions or {}
        self._validate = validate
        self.guidance = guidance
        self.min_images = min_images

    def preview(self, output: BaseModel) -> str:
        return self._render(output)

    def validate(self, output: BaseModel, media: dict[str, FileRef] | None = None) -> list[str]:
        """
        Hard checks in code: this output type's own rules, the images it must carry (known ids
        from this task's `media`), then placeholder text in any field.
        """
        errors = self._validate(output) if self._validate else []
        return errors + self._image_errors(output, media or {}) + _placeholder_errors(output)

    def _image_errors(self, output: BaseModel, media: dict[str, FileRef]) -> list[str]:
        if not self.min_images:
            return []
        ids = getattr(output, "images", [])
        if len(ids) < self.min_images:
            return ["The draft has no image: make one with generate_image and give its id."]
        unknown = [file_id for file_id in ids if file_id not in media]
        if unknown:
            return [
                f"Unknown image ids: {', '.join(unknown)}. Use the ids generate_image returned."
            ]
        return []

    def images(self, output: BaseModel, media: dict[str, FileRef] | None = None) -> list[FileRef]:
        """
        The output's image ids as the real files: unknown ids and non-images are dropped, so the
        model never makes up a FileRef.
        """
        media = media or {}
        ids = dict.fromkeys(getattr(output, "images", []))
        return [
            media[file_id]
            for file_id in ids
            if file_id in media and media[file_id].kind == FileKind.IMAGE
        ][:MAX_IMAGES]

    def to_planned_action(
        self, output: BaseModel, action: str | None, media: dict[str, FileRef] | None = None
    ) -> PlannedAction | None:
        if action is None:
            return None
        if action not in self._actions:
            raise ValueError(f"Output {self.name!r} can't be used for action {action!r}")
        return self._actions[action](output, self.images(output, media))

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

def _validate_images(output: BaseModel) -> list[str]:
    count = len(getattr(output, "images", []))
    if count > MAX_IMAGES:
        return [f"The draft has {count} images; at most {MAX_IMAGES} fit."]
    return []

def _validate_post(output: SocialPostOutput) -> list[str]:
    if not output.text.strip():
        return ["The post is empty."]
    if len(output.text) > POST_LIMIT:
        return [f"The post is {len(output.text)} characters; Bluesky allows {POST_LIMIT}."]
    return _validate_images(output)

def _validate_email(output: EmailOutput) -> list[str]:
    errors = []
    if "@" not in output.to:
        errors.append(f"The recipient {output.to!r} is not an email address or list.")
    if not output.subject.strip():
        errors.append("The email has no subject line.")
    if not output.body.strip():
        errors.append("The email body is empty.")
    return errors + _validate_images(output)

def _validate_report(output: ReportOutput) -> list[str]:
    errors = []
    if not output.summary.strip():
        errors.append("The report summary is empty.")
    if not output.sources:
        errors.append("The report lists no sources.")
    return errors

def _validate_notes(output: NotesOutput) -> list[str]:
    return [] if output.notes.strip() else ["The notes are empty."]

def _validate_image(output: ImageOutput) -> list[str]:
    errors = [] if output.caption.strip() else ["The image has no caption."]
    return errors + _validate_images(output)

OUTPUTS: dict[str, OutputType] = {
    "social_post": OutputType(
        "social_post",
        SocialPostOutput,
        render=lambda output: output.text,
        actions={
            "post_social": lambda output, images: PostSocial(text=output.text, images=images)
        },
        validate=_validate_post,
        guidance=(
            f"One Bluesky post of at most {POST_LIMIT} characters, counting spaces and line "
            "breaks; aim for about 250. The first line is the hook and has to stop the scroll. "
            "One idea, one link at most, no more than two hashtags. Plain text, no placeholders."
        ),
    ),
    "email": OutputType(
        "email",
        EmailOutput,
        render=_render_email,
        actions={
            "send_email": lambda output, images: SendEmail(
                to=output.to, subject=output.subject, body=output.body, images=images
            )
        },
        validate=_validate_email,
        guidance=(
            "An email with a recipient address (the newsletter address or list from what you "
            "know), a subject line under 60 characters that makes them want to open it (a "
            "benefit, a tension or a specific detail, not a generic announcement), an opening "
            "line that pulls them in, short paragraphs, and one call to action that starts with "
            "a verb."
        ),
    ),
    "report": OutputType(
        "report",
        ReportOutput,
        render=lambda output: _render_with_sources(output.summary, output.sources),
        validate=_validate_report,
        guidance="A short summary for the founder, with a source URL for every claim.",
    ),
    "notes": OutputType(
        "notes",
        NotesOutput,
        render=lambda output: _render_with_sources(output.notes, output.sources),
        validate=_validate_notes,
        guidance="Notes for the next step of the task: what you found, with the source URLs.",
    ),
    "image": OutputType(
        "image",
        ImageOutput,
        render=lambda output: output.caption,
        validate=_validate_image,
        guidance=(
            f"The ids of the images you made with generate_image (1 to {MAX_IMAGES}), and a "
            "caption of one or two lines telling the founder what the image is for."
        ),
        min_images=1,
    ),
}

ACTIONS: dict[str, str] = {"post_social": "social_post", "send_email": "email"}

TOOL_NAMES: frozenset[str] = frozenset(
    {"read_memory", "web_search", "fetch_page", "generate_image"}
)
