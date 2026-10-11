import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from contract import Error, Event

log = logging.getLogger(__name__)

GENERIC_ERROR = "Something went wrong on my side. Try again in a moment."
OUT_OF_CREDITS = (
    "⚠️ The AI account is out of credits, so the team can't work right now. Top up at "
    "openrouter.ai, then send your message again."
)
MAX_REQUEST_CHARS = 20_000
CUT = "\n[…cut: the rest of the message was too long]"

class OutOfCredits(BaseException):
    """
    OpenRouter refused a call for lack of credits. A BaseException on purpose: the brain's
    fallbacks (`except Exception`) must not catch it and carry on with other models or rules;
    it ends the run, and `guarded` turns it into one clear Error.
    """

def is_out_of_credits(status: int, body: str) -> bool:
    return status == 402 or "insufficient credits" in body.lower()

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
    except OutOfCredits:
        log.error("OpenRouter is out of credits")
        yield Error(team_id=team_id, message=OUT_OF_CREDITS, recoverable=True)
    except Exception:
        log.exception("Brain stream failed")
        yield Error(team_id=team_id, message=GENERIC_ERROR, recoverable=True)
