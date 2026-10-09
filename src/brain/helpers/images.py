import base64
from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import BaseModel

from brain.helpers.llm import Usage

if TYPE_CHECKING:
    from brain.deps import Settings

RESOLUTION = "1K"
ATTEMPTS = 2

class GeneratedImage(BaseModel):
    data: bytes
    mime_type: str
    usage: Usage = Usage()

class ImageError(Exception):
    pass

class ImageClient(Protocol):
    async def generate(
        self,
        model: str,
        prompt: str,
        aspect_ratio: str = "1:1",
        references: list[tuple[bytes, str]] | None = None,
    ) -> GeneratedImage: ...

class OpenRouterImages:
    """
    One image from a prompt through OpenRouter's Images API (not chat completions).
    `references` are (bytes, mime type) pairs for models that take reference images.
    """

    def __init__(self, settings: "Settings", transport: httpx.AsyncBaseTransport | None = None):
        self._url = f"{settings.openrouter_base_url.rstrip('/')}/images"
        self._key = settings.openrouter_api_key
        self._timeout = settings.llm_timeout
        self._transport = transport

    async def generate(
        self,
        model: str,
        prompt: str,
        aspect_ratio: str = "1:1",
        references: list[tuple[bytes, str]] | None = None,
    ) -> GeneratedImage:
        if self._key is None:
            raise ImageError("OPENROUTER_API_KEY is not set")
        body: dict = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": RESOLUTION,
            "n": 1,
        }
        if references:
            body["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"},
                }
                for data, mime in references
            ]
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        problem = "no attempt"
        for _ in range(ATTEMPTS):
            try:
                client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
                async with client:
                    response = await client.post(self._url, json=body, headers=headers)
            except httpx.HTTPError as error:
                problem = type(error).__name__
                continue
            if response.status_code == 200:
                return _parse(response)
            problem = f"HTTP {response.status_code}"
        raise ImageError(f"{model}: {problem}")

def _parse(response: httpx.Response) -> GeneratedImage:
    try:
        data = response.json()
        first = data["data"][0]
        image = base64.b64decode(first["b64_json"])
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ImageError("The Images API returned no image") from error
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    return GeneratedImage(
        data=image,
        mime_type=first.get("media_type") or "image/png",
        usage=Usage(
            input_tokens=usage.get("prompt_tokens") or 0,
            output_tokens=usage.get("completion_tokens") or 0,
            cost=float(cost) if cost is not None else None,
        ),
    )
