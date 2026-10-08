from typing import Protocol

from pydantic import BaseModel

from contract import Store

class TelegramTopic(BaseModel):
    team_id: str
    business_id: str
    chat_id: int
    thread_id: int
    name: str

class AppStore(Store, Protocol):
    """
    The contract Store plus what only the app needs: businesses and their Telegram links.
    """

    async def create_business(self, chat_id: int) -> str: ...
    async def list_businesses(self) -> dict[int, str]: ...
    async def save_topic(self, topic: TelegramTopic) -> None: ...
    async def list_topics(self) -> list[TelegramTopic]: ...
