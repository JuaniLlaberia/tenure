import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from contract import Error, Event

log = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong on my side. Try again in a moment."
MAX_REQUEST_CHARS = 20_000
CUT = "\n[…cut: the rest of the message was too long]"

def new_id() -> str:
    return str(uuid4())

def utcnow() -> datetime:
    return datetime.now(UTC)

def clip_request(text: str) -> str:
    """
    A founder's message as the models see it: an over-long paste keeps its start, marked cut.
    """
    if len(text) <= MAX_REQUEST_CHARS:
        return text
    return text[: MAX_REQUEST_CHARS - len(CUT)] + CUT

async def guarded(events: AsyncIterator[Event], team_id: str | None = None) -> AsyncIterator[Event]:
    """
    Passes events through; any exception becomes one recoverable Error, so streams never raise.
    """
    try:
        async for event in events:
            yield event
    except Exception:
        log.exception("Brain stream failed")
        yield Error(team_id=team_id, message=GENERIC_ERROR, recoverable=True)
