from typing import Protocol

from pydantic import BaseModel

from contract.models.actions import ActionResult
from contract.models.files import FileRef

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

class PageContent(BaseModel):
    url: str
    title: str | None
    text: str

class Tools(Protocol):
    async def post_social(
        self, business_id: str, text: str, images: list[FileRef] | None = None
    ) -> ActionResult: ...
    async def delete_social(self, business_id: str, external_id: str) -> ActionResult: ...
    async def send_email(
        self,
        business_id: str,
        to: str,
        subject: str,
        body: str,
        images: list[FileRef] | None = None,
    ) -> ActionResult: ...
    async def web_search(self, query: str, k: int = 5) -> list[SearchResult]: ...
    async def fetch_page(self, url: str) -> PageContent | None: ...
    async def save_file(
        self,
        business_id: str,
        data: bytes,
        mime_type: str,
        name: str | None = None,
        alt_text: str | None = None,
    ) -> FileRef | None: ...
    async def read_file(self, business_id: str, file_id: str) -> bytes | None: ...
