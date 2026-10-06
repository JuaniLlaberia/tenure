from typing import Protocol

from pydantic import BaseModel

from contract.models.actions import ActionResult

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

class PageContent(BaseModel):
    url: str
    title: str | None
    text: str

class Tools(Protocol):
    async def post_social(self, business_id: str, text: str) -> ActionResult: ...
    async def delete_social(self, business_id: str, external_id: str) -> ActionResult: ...
    async def send_email(
        self, business_id: str, to: str, subject: str, body: str
    ) -> ActionResult: ...
    async def web_search(self, query: str, k: int = 5) -> list[SearchResult]: ...
    async def fetch_page(self, url: str) -> PageContent | None: ...
