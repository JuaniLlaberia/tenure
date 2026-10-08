import re
from collections.abc import Callable
from typing import Any

from atproto import AsyncClient, client_utils
from atproto_client.exceptions import (
    BadRequestError,
    InvokeTimeoutError,
    LoginRequiredError,
    NetworkError,
    UnauthorizedError,
)

TOKEN = re.compile(r"https?://\S+|(?<![\w#])#[A-Za-z]\w*")
TRAILING = ".,!?;:)'\""

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

def post_url(handle: str, uri: str) -> str:
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"

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

    async def post(self, text: str) -> tuple[str, str]:
        """
        Posts and returns (post URI, public URL).
        """
        response = await self._call(lambda client: client.send_post(rich_text(text)))
        return response.uri, post_url(self.handle, response.uri)

    async def delete(self, uri: str) -> None:
        await self._call(lambda client: client.delete_post(uri))
