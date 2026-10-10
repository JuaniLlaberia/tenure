from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None
    url: str | None = None
    copy: str | None = None

Keyboard = list[list[Button]]

@dataclass(frozen=True)
class IncomingFile:
    """
    A file the founder sent: Telegram's id to download it, and what Telegram says about it.
    """

    telegram_id: str
    mime_type: str
    name: str | None = None
    size: int | None = None

class TopicGone(Exception):
    """
    Telegram no longer has the topic a message was sent to: the founder deleted it.
    """

    def __init__(self, thread_id: int | None) -> None:
        super().__init__(f"Topic {thread_id} is gone")
        self.thread_id = thread_id

class Chat(Protocol):
    """
    What the flows need from Telegram. The real one wraps the bot; tests use a fake.
    """

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, keyboard: Keyboard | None = None
    ) -> int: ...
    async def edit(
        self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None
    ) -> None: ...
    async def delete(self, chat_id: int, message_id: int) -> None: ...
    async def create_topic(self, chat_id: int, name: str, icon: str | None = None) -> int: ...
    async def delete_topic(self, chat_id: int, thread_id: int) -> None: ...
    async def pin(self, chat_id: int, message_id: int) -> None: ...
    async def typing(self, chat_id: int, thread_id: int | None) -> None: ...
    async def download(self, telegram_id: str) -> bytes: ...
    async def can_manage_topics(self, chat_id: int) -> bool: ...
    async def send_photo(
        self,
        chat_id: int,
        thread_id: int | None,
        photo: bytes,
        caption: str = "",
        keyboard: Keyboard | None = None,
    ) -> int: ...
    async def send_album(
        self, chat_id: int, thread_id: int | None, photos: list[bytes], caption: str = ""
    ) -> list[int]: ...
