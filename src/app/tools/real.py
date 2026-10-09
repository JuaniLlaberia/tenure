"""
The app's Tools implementation: real Bluesky, Resend email, search and fetch.
Search uses Keenable while it answers and DuckDuckGo after that (or for a single failed search).
Expected failures come back as ActionResult(ok=False) or empty results, never as exceptions.
One Bluesky account and one email sender for every business, for now. Files (founder uploads
and the brain's images) are read and stored through Files.
"""

import logging
import os
from uuid import uuid4

from app.files import Files
from app.tools import images
from app.tools.bluesky import Bluesky, Image
from app.tools.email import Inline, Resend
from app.tools.keenable import Keenable, KeenableError
from app.tools.web import Web
from contract import ActionResult, FileKind, FileRef, PageContent, SearchResult

logger = logging.getLogger(__name__)

NO_BLUESKY = "Bluesky isn't connected. Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD."
NO_EMAIL = "Email isn't connected. Set RESEND_API_KEY and RESEND_FROM."
NO_FILES = "Files aren't available."
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}

class MissingImage(Exception):
    pass

def _new_id() -> str:
    return str(uuid4())

def _reason(error: Exception) -> str:
    return " ".join(str(error).split())[:200] or type(error).__name__

class RealTools:
    def __init__(
        self,
        bluesky: Bluesky | None = None,
        email: Resend | None = None,
        web: Web | None = None,
        keenable: Keenable | None = None,
        files: Files | None = None,
    ) -> None:
        self._bluesky = bluesky
        self._email = email
        self._web = web or Web()
        self._keenable = keenable
        self._files = files

    @classmethod
    def from_env(cls, files: Files | None = None) -> "RealTools":
        handle = os.environ.get("BLUESKY_HANDLE")
        password = os.environ.get("BLUESKY_APP_PASSWORD")
        api_key = os.environ.get("RESEND_API_KEY")
        sender = os.environ.get("RESEND_FROM")
        keenable_key = os.environ.get("KEENABLE_API_KEY")
        bluesky = Bluesky(handle, password) if handle and password else None
        email = Resend(api_key, sender) if api_key and sender else None
        keenable = Keenable(keenable_key) if keenable_key else None
        logger.info(
            "Tools: Bluesky %s, email %s, search %s",
            "on" if bluesky else "off",
            "on" if email else "off",
            "Keenable then DuckDuckGo" if keenable else "DuckDuckGo",
        )
        return cls(bluesky, email, keenable=keenable, files=files)

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

    async def post_social(
        self, business_id: str, text: str, images: list[FileRef] | None = None
    ) -> ActionResult:
        action_id = _new_id()
        if self._bluesky is None:
            return ActionResult(action_id=action_id, ok=False, error=NO_BLUESKY)
        try:
            uri, url = await self._bluesky.post(text, await self._post_images(business_id, images))
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
        self,
        business_id: str,
        to: str,
        subject: str,
        body: str,
        images: list[FileRef] | None = None,
    ) -> ActionResult:
        action_id = _new_id()
        if self._email is None:
            return ActionResult(action_id=action_id, ok=False, error=NO_EMAIL)
        try:
            inline = await self._email_images(business_id, images)
            email_id = await self._email.send(to, subject, body, inline)
        except Exception as error:
            logger.exception("Sending email failed")
            return ActionResult(action_id=action_id, ok=False, error=_reason(error))
        return ActionResult(action_id=action_id, ok=True, external_id=email_id)

    async def save_file(
        self,
        business_id: str,
        data: bytes,
        mime_type: str,
        name: str | None = None,
        alt_text: str | None = None,
    ) -> FileRef | None:
        if self._files is None:
            return None
        try:
            return await self._files.save(
                business_id, data, mime_type, name=name, alt_text=alt_text
            )
        except Exception:
            logger.exception("Saving a file failed")
            return None

    async def read_file(self, business_id: str, file_id: str) -> bytes | None:
        if self._files is None:
            return None
        try:
            return await self._files.read(business_id, file_id)
        except Exception:
            logger.exception("Reading file %s failed", file_id)
            return None

    async def _image_bytes(self, business_id: str, ref: FileRef) -> bytes:
        if ref.kind != FileKind.IMAGE:
            raise MissingImage(f"{ref.name or ref.file_id} isn't an image")
        data = await self.read_file(business_id, ref.file_id)
        if data is None:
            raise MissingImage(f"Couldn't find the image {ref.name or ref.file_id}")
        return data

    async def _post_images(self, business_id: str, refs: list[FileRef] | None) -> list[Image]:
        found = []
        for ref in refs or []:
            data, _ = images.fit(await self._image_bytes(business_id, ref), ref.mime_type)
            width, height = images.size_of(data)
            found.append(Image(data, ref.alt_text or "", width, height))
        return found

    async def _email_images(self, business_id: str, refs: list[FileRef] | None) -> list[Inline]:
        found = []
        for n, ref in enumerate(refs or [], start=1):
            data = await self._image_bytes(business_id, ref)
            filename = ref.name or f"image-{n}.{EXTENSIONS.get(ref.mime_type, 'png')}"
            found.append(Inline(f"img{n}", filename, data, ref.alt_text or ""))
        return found

    async def web_search(self, query: str, k: int = 5) -> list[SearchResult]:
        results = await self._keenable_search(query, k)
        if results:
            return results
        try:
            return await self._web.search(query, k)
        except Exception:
            logger.exception("Web search failed")
            return []

    async def _keenable_search(self, query: str, k: int) -> list[SearchResult]:
        if self._keenable is None:
            return []
        try:
            return await self._keenable.search(query, k)
        except KeenableError as error:
            if error.out:
                logger.warning("%s. Searching with DuckDuckGo from now on", _reason(error))
                self._keenable = None
            else:
                logger.warning("%s. Using DuckDuckGo for this search", _reason(error))
        except Exception as error:
            logger.warning("Keenable search failed (%s). Using DuckDuckGo", _reason(error))
        return []

    async def fetch_page(self, url: str) -> PageContent | None:
        try:
            return await self._web.fetch(url)
        except Exception:
            logger.exception("Fetching %s failed", url)
            return None
