from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None
    url: str | None = None
    copy: str | None = None

Keyboard = list[list[Button]]

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
    async def create_topic(self, chat_id: int, name: str) -> int: ...
    async def pin(self, chat_id: int, message_id: int) -> None: ...
    async def typing(self, chat_id: int, thread_id: int | None) -> None: ...
