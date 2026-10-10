import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from atproto import AsyncClient, client_utils, models
from atproto_client.exceptions import (
    BadRequestError,
    InvokeTimeoutError,
    LoginRequiredError,
    NetworkError,
    UnauthorizedError,
)

TOKEN = re.compile(r"https?://\S+|(?<![\w#])#[A-Za-z]\w*")
TRAILING = ".,!?;:)'\""
RECENT_POSTS = 30
TIMED_OUT_WITHIN = timedelta(minutes=10)

def rich_text(text: str) -> client_utils.TextBuilder:
    """
    Turns links and hashtags into Bluesky facets so they're clickable.
    """
    builder = client_utils.TextBuilder()
    position = 0
    for match in TOKEN.finditer(text):
        token = match.group(0).rstrip(TRAILING)
        if len(token) < 2:
            continue
        builder.text(text[position : match.start()])
        if token.startswith("#"):
            builder.tag(token, token[1:])
        else:
            builder.link(token, token)
        position = match.start() + len(token)
    builder.text(text[position:])
    return builder

def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())

def _when(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)

def post_url(handle: str, uri: str) -> str:
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"

@dataclass(frozen=True)
class Image:
    data: bytes
    alt: str
    width: int
    height: int

class Bluesky:
    """
    One Bluesky account, signed in lazily with an app password. If the session has expired it
    signs in again and retries once; other failures aren't retried, so a post is never doubled.
    """

    def __init__(
        self, handle: str, app_password: str, make_client: Callable[[], Any] = AsyncClient
    ) -> None:
        self.handle = handle.lstrip("@")
        self._password = app_password
        self._make_client = make_client
        self._client: Any = None

    async def _signed_in(self) -> Any:
        if self._client is None:
            client = self._make_client()
            try:
                await client.login(self.handle, self._password)
            except (InvokeTimeoutError, NetworkError):
                await client.login(self.handle, self._password)
            self._client = client
        return self._client

    async def sign_in(self) -> None:
        await self._signed_in()

    async def _call(self, action: Callable[[Any], Any]) -> Any:
        try:
            return await action(await self._signed_in())
        except (UnauthorizedError, LoginRequiredError):
            pass
        except BadRequestError as error:
            if "token" not in str(error).lower():
                raise
        self._client = None
        return await action(await self._signed_in())

    async def find(self, text: str, within: timedelta) -> tuple[str, str] | None:
        """
        (URI, public URL) of this account's own post with the same text from the last
        `within`, if there is one. Deleted posts aren't in the feed, so an undone post can go
        out again.
        """
        response = await self._call(
            lambda client: client.get_author_feed(
                self.handle, limit=RECENT_POSTS, filter="posts_no_replies"
            )
        )
        since = datetime.now(UTC) - within
        for item in getattr(response, "feed", None) or []:
            if getattr(item, "reason", None) is not None:
                continue
            record = item.post.record
            when = _when(getattr(record, "created_at", None))
            if when and when >= since and _same(getattr(record, "text", ""), text):
                return item.post.uri, post_url(self.handle, item.post.uri)
        return None

    async def post(self, text: str, images: list[Image] | None = None) -> tuple[str, str]:
        """
        Posts, with up to 4 images already under Bluesky's size limit, and returns
        (post URI, public URL). When the call times out, the post may still have gone through,
        so the account's recent posts are checked before reporting the failure.
        """
        try:
            return await self._send(text, images)
        except (InvokeTimeoutError, NetworkError):
            try:
                found = await self.find(text, TIMED_OUT_WITHIN)
            except Exception:
                found = None
            if found is None:
                raise
            return found

    async def _send(self, text: str, images: list[Image] | None) -> tuple[str, str]:
        if images:
            response = await self._call(
                lambda client: client.send_images(
                    rich_text(text),
                    images=[image.data for image in images],
                    image_alts=[image.alt for image in images],
                    image_aspect_ratios=[
                        models.AppBskyEmbedDefs.AspectRatio(width=image.width, height=image.height)
                        for image in images
                    ],
                )
            )
        else:
            response = await self._call(lambda client: client.send_post(rich_text(text)))
        return response.uri, post_url(self.handle, response.uri)

    async def delete(self, uri: str) -> None:
        await self._call(lambda client: client.delete_post(uri))
