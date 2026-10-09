"""
A business's files: what the founder sends in Telegram and the images the brain makes.
The bytes and their FileRef live in the AppStore; this adds ids, kinds and the size limit.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.store.base import AppStore
from contract import FileKind, FileRef

MAX_DOWNLOAD = 20 * 1024 * 1024
DOCUMENT_TYPES = ("application/pdf", "text/")
AVATARS = Path(__file__).resolve().parents[2] / "assets" / "avatars"

def avatar_bytes(avatar: str | None) -> bytes | None:
    """
    A persona's picture from assets/avatars, or None if it has none or the file is missing.
    """
    if not avatar:
        return None
    path = (AVATARS / avatar).resolve()
    if not path.is_relative_to(AVATARS) or not path.is_file():
        return None
    return path.read_bytes()

def kind_of(mime_type: str) -> FileKind:
    family = mime_type.split("/", 1)[0]
    if family in ("image", "audio", "video"):
        return FileKind(family)
    return FileKind.DOCUMENT

def _utcnow() -> datetime:
    return datetime.now(UTC)

class Files:
    def __init__(self, store: AppStore, clock: Callable[[], datetime] = _utcnow) -> None:
        self._store = store
        self._clock = clock

    async def save(
        self,
        business_id: str,
        data: bytes,
        mime_type: str,
        *,
        name: str | None = None,
        source: Literal["founder", "generated"] = "generated",
        alt_text: str | None = None,
    ) -> FileRef:
        ref = FileRef(
            file_id=str(uuid4()),
            business_id=business_id,
            kind=kind_of(mime_type),
            mime_type=mime_type,
            name=name,
            size_bytes=len(data),
            source=source,
            alt_text=alt_text,
            created_at=self._clock(),
        )
        await self._store.store_file(ref, data)
        return ref

    async def read(self, business_id: str, file_id: str) -> bytes | None:
        """
        The file's bytes, or None if it's missing or belongs to another business.
        """
        if await self._store.get_file(business_id, file_id) is None:
            return None
        return await self._store.file_bytes(business_id, file_id)
