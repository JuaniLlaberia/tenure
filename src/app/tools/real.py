"""
The app's Tools implementation: real Bluesky, Resend email, search and fetch.
Expected failures come back as ActionResult(ok=False) or empty results, never as exceptions.
One Bluesky account and one email sender for every business, for now.
"""

import logging
import os
from uuid import uuid4

from app.tools.bluesky import Bluesky
from app.tools.email import Resend
from app.tools.web import Web
from contract import ActionResult, PageContent, SearchResult

logger = logging.getLogger(__name__)

NO_BLUESKY = "Bluesky isn't connected. Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD."
NO_EMAIL = "Email isn't connected. Set RESEND_API_KEY and RESEND_FROM."

def _new_id() -> str:
    return str(uuid4())

def _reason(error: Exception) -> str:
    return " ".join(str(error).split())[:200] or type(error).__name__

class RealTools:
    def __init__(
        self, bluesky: Bluesky | None = None, email: Resend | None = None, web: Web | None = None
    ) -> None:
        self._bluesky = bluesky
        self._email = email
        self._web = web or Web()

    @classmethod
    def from_env(cls) -> "RealTools":
        handle = os.environ.get("BLUESKY_HANDLE")
        password = os.environ.get("BLUESKY_APP_PASSWORD")
        api_key = os.environ.get("RESEND_API_KEY")
        sender = os.environ.get("RESEND_FROM")
        bluesky = Bluesky(handle, password) if handle and password else None
        email = Resend(api_key, sender) if api_key and sender else None
        logger.info(
            "Tools: Bluesky %s, email %s, search DuckDuckGo",
            "on" if bluesky else "off",
            "on" if email else "off",
        )
        return cls(bluesky, email)

    async def check(self) -> None:
        """
        Signs in to Bluesky at startup so bad credentials show up in the log right away.
        """
        if self._bluesky is None:
            return
        try:
            await self._bluesky.sign_in()
            logger.info("Signed in to Bluesky as %s", self._bluesky.handle)
        except Exception as error:
            logger.error("Bluesky sign-in failed: %s", _reason(error))

    async def post_social(self, business_id: str, text: str) -> ActionResult:
        action_id = _new_id()
        if self._bluesky is None:
            return ActionResult(action_id=action_id, ok=False, error=NO_BLUESKY)
        try:
            uri, url = await self._bluesky.post(text)
        except Exception as error:
            logger.exception("Posting to Bluesky failed")
            return ActionResult(action_id=action_id, ok=False, error=_reason(error))
        return ActionResult(action_id=action_id, ok=True, url=url, external_id=uri)

    async def delete_social(self, business_id: str, external_id: str) -> ActionResult:
        action_id = _new_id()
        if self._bluesky is None:
            return ActionResult(action_id=action_id, ok=False, error=NO_BLUESKY)
        try:
            await self._bluesky.delete(external_id)
        except Exception as error:
            logger.exception("Deleting the Bluesky post failed")
            return ActionResult(action_id=action_id, ok=False, error=_reason(error))
        return ActionResult(action_id=action_id, ok=True, external_id=external_id)

    async def send_email(
        self, business_id: str, to: str, subject: str, body: str
    ) -> ActionResult:
        action_id = _new_id()
        if self._email is None:
            return ActionResult(action_id=action_id, ok=False, error=NO_EMAIL)
        try:
            email_id = await self._email.send(to, subject, body)
        except Exception as error:
            logger.exception("Sending email failed")
            return ActionResult(action_id=action_id, ok=False, error=_reason(error))
        return ActionResult(action_id=action_id, ok=True, external_id=email_id)

    async def web_search(self, query: str, k: int = 5) -> list[SearchResult]:
        try:
            return await self._web.search(query, k)
        except Exception:
            logger.exception("Web search failed")
            return []

    async def fetch_page(self, url: str) -> PageContent | None:
        try:
            return await self._web.fetch(url)
        except Exception:
            logger.exception("Fetching %s failed", url)
            return None
