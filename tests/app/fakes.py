from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.chat.port import Keyboard

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

@dataclass
class Sent:
    message_id: int
    thread_id: int | None
    text: str
    keyboard: Keyboard = field(default_factory=list)

    def data(self, label: str) -> str:
        for row in self.keyboard:
            for button in row:
                if label in button.text:
                    return button.data
        raise AssertionError(f"No button {label!r} in {self.keyboard}")

class FakeChat:
    def __init__(self) -> None:
        self.messages: dict[int, Sent] = {}
        self.deleted: list[int] = []
        self.topics: dict[str, int] = {}
        self.pinned: list[int] = []
        self.typing_calls = 0

    async def send(self, chat_id, thread_id, text, keyboard=None) -> int:
        message_id = len(self.messages) + len(self.deleted) + 1
        self.messages[message_id] = Sent(message_id, thread_id, text, keyboard or [])
        return message_id

    async def edit(self, chat_id, message_id, text, keyboard=None) -> None:
        self.messages[message_id].text = text
        self.messages[message_id].keyboard = keyboard or []

    async def delete(self, chat_id, message_id) -> None:
        del self.messages[message_id]
        self.deleted.append(message_id)

    async def create_topic(self, chat_id, name) -> int:
        self.topics[name] = 100 + len(self.topics)
        return self.topics[name]

    async def pin(self, chat_id, message_id) -> None:
        self.pinned.append(message_id)

    async def typing(self, chat_id, thread_id) -> None:
        self.typing_calls += 1

    def thread(self, thread_id: int | None) -> list[Sent]:
        return [m for m in self.messages.values() if m.thread_id == thread_id]

    def last(self, thread_id: int | None) -> Sent:
        return self.thread(thread_id)[-1]

    def find(self, part: str) -> Sent:
        matches = [m for m in self.messages.values() if part in m.text]
        assert matches, f"No message containing {part!r}"
        return matches[-1]

class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now
