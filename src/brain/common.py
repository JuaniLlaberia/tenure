import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from contract import Error, Event

log = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong on my side. Try again in a moment."

def new_id() -> str:
    return str(uuid4())

def utcnow() -> datetime:
    return datetime.now(UTC)

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
