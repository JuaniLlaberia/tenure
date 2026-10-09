from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

class FileKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"

class FileRef(BaseModel):
    file_id: str
    business_id: str
    kind: FileKind
    mime_type: str
    name: str | None = None
    size_bytes: int = 0
    source: Literal["founder", "generated"]
    alt_text: str | None = None
    created_at: datetime
