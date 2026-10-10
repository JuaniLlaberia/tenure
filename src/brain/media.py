import base64
import logging
from typing import Literal

from pydantic import BaseModel

from brain.context import MAX_FETCH_CHARS
from brain.deps import Deps
from brain.helpers.llm import Message
from contract import FileKind, FileRef, Persona

log = logging.getLogger(__name__)

Mode = Literal["audio", "image", "pdf", "text", "unsupported"]

UNREADABLE = "couldn't open it"
VIDEO_REPLY = "I can't watch videos yet; tell me what's in it."
UNHEARD_REPLY = "I couldn't hear your voice note. Could you send it again, or type it?"
TEXT_TYPES = ("application/json", "application/csv", "application/xml")
AUDIO_FORMATS = {
    "ogg": "ogg",
    "opus": "ogg",
    "mpeg": "mp3",
    "mp3": "mp3",
    "wav": "wav",
    "x-wav": "wav",
    "aac": "aac",
    "flac": "flac",
    "aiff": "aiff",
    "mp4": "m4a",
    "m4a": "m4a",
    "x-m4a": "m4a",
    "mp4a-latm": "m4a",
}
PROMPTS = {
    "audio": "Transcribe this voice note word for word. Reply with the transcript only.",
    "image": (
        "Describe this photo in one sentence; read out any text in it. Reply with the "
        "description only."
    ),
    "pdf": (
        "Extract the text of this document; keep prices, dates and names exact. Reply with the "
        "text only."
    ),
}
STATUSES = {
    "audio": "{name} is listening to your voice note…",
    "image": "{name} is looking at your photo…",
    "pdf": "{name} is reading your document…",
    "text": "{name} is reading your document…",
}

class MediaText(BaseModel):
    """
    What an attachment says, as text: a transcript, a description or a document's text.
    Empty when the file couldn't be read.
    """

    file: FileRef
    text: str

def mode(file: FileRef) -> Mode | None:
    """
    How a file is read; None for videos, which the team can't watch yet.
    """
    mime = file.mime_type.lower()
    if file.kind == FileKind.VIDEO or mime.startswith("video/"):
        return None
    if file.kind == FileKind.AUDIO or mime.startswith("audio/"):
        return "audio"
    if file.kind == FileKind.IMAGE or mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or mime in TEXT_TYPES:
        return "text"
    return "unsupported"

def status(persona: Persona, file: FileRef) -> str | None:
    template = STATUSES.get(mode(file) or "")
    return template.format(name=persona.name) if template else None

async def describe(deps: Deps, business_id: str, file: FileRef) -> MediaText | None:
    """
    Reads one attachment through Tools.read_file and turns it into text. A file that can't be
    read or understood gives empty text, so the run goes on.
    """
    how = mode(file)
    if how is None:
        return None
    if how == "unsupported":
        return MediaText(file=file, text="")
    try:
        data = await deps.tools.read_file(business_id, file.file_id)
    except Exception as error:
        log.warning("Couldn't read file %s: %s", file.file_id, error)
        data = None
    if data is None:
        return MediaText(file=file, text="")
    if how == "text":
        text = data.decode("utf-8", errors="replace")
    else:
        text = await _ask_model(deps, how, file, data)
    text = text.strip()[:MAX_FETCH_CHARS]
    if how == "image" and text and not file.alt_text:
        file = file.model_copy(update={"alt_text": text})
    return MediaText(file=file, text=text)

async def _ask_model(deps: Deps, how: Mode, file: FileRef, data: bytes) -> str:
    """
    The media model chooses how much to think: Gemini 3.5 Flash Lite can't turn reasoning off.
    """
    content = [{"type": "text", "text": PROMPTS[how]}, _part(how, file, data)]
    messages: list[Message] = [{"role": "user", "content": content}]
    try:
        completion = await deps.llm.complete(deps.settings.model_media, messages)
    except Exception as error:
        log.warning("Media model couldn't read %s: %s", file.file_id, error)
        return ""
    return completion.text or ""

def _part(how: Mode, file: FileRef, data: bytes) -> dict:
    encoded = base64.b64encode(data).decode("ascii")
    if how == "audio":
        subtype = file.mime_type.lower().split("/")[-1].split(";")[0]
        audio_format = AUDIO_FORMATS.get(subtype, subtype)
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}}
    if how == "image":
        url = f"data:{file.mime_type};base64,{encoded}"
        return {"type": "image_url", "image_url": {"url": url}}
    return {
        "type": "file",
        "file": {
            "filename": file.name or "document.pdf",
            "file_data": f"data:application/pdf;base64,{encoded}",
        },
    }

def block(described: MediaText) -> str:
    """
    One attachment as a text block for the request: "[Voice note] …", "[Photo f1: …]",
    "[Document price-list.pdf]\\n…".
    """
    file, text = described.file, described.text
    how = mode(file)
    if how == "audio":
        return f"[Voice note] {text}" if text else f"[Voice note: {UNREADABLE}]"
    if how == "image":
        return f"[Photo {file.file_id}: {text or UNREADABLE}]"
    name = file.name or "document"
    return f"[Document {name}]\n{text}" if text else f"[Document {name}: {UNREADABLE}]"

def attachments_text(text: str, described: list[MediaText]) -> str:
    """
    The founder's message with its attachments as text. Without a caption, a voice note's
    transcript is the message itself.
    """
    caption = text.strip()
    parts = [caption] if caption else []
    for item in described:
        if not caption and mode(item.file) == "audio" and item.text:
            parts.append(item.text)
        else:
            parts.append(block(item))
    return "\n\n".join(parts)

def unheard(described: list[MediaText]) -> bool:
    """
    A voice note that couldn't be transcribed.
    """
    return any(mode(item.file) == "audio" and not item.text for item in described)

def nothing_heard(text: str, described: list[MediaText]) -> bool:
    """
    No caption and only voice notes that couldn't be transcribed: nothing for the team to act
    on, so the run doesn't start.
    """
    return (
        not text.strip()
        and bool(described)
        and all(mode(item.file) == "audio" and not item.text for item in described)
    )

def photos(described: list[MediaText]) -> dict[str, FileRef]:
    """
    The founder's photos that could be read, by file id: what the writer may attach.
    """
    return {
        item.file.file_id: item.file
        for item in described
        if mode(item.file) == "image" and item.text
    }

def onboarding_answer(text: str, described: list[MediaText]) -> str | dict:
    """
    An answer to an onboarding question: voice notes become its text; photos and documents go
    to profile extraction next to fetched pages, titled by file name.
    """
    spoken = [item for item in described if mode(item.file) == "audio"]
    files = [item for item in described if mode(item.file) != "audio"]
    answer = attachments_text(text, spoken)
    if not files:
        return answer
    titled = [(item, item.file.name or f"photo {item.file.file_id}") for item in files]
    notes = [f"[Sent {title}]" if item.text else block(item) for item, title in titled]
    return {
        "text": "\n\n".join(part for part in [answer, *notes] if part),
        "files": [{"title": title, "text": item.text} for item, title in titled if item.text],
    }
