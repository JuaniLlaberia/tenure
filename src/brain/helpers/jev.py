from typing import TYPE_CHECKING, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from brain.deps import Settings

class JevAnswer(BaseModel):
    type: Literal["noul", "choice", "score"]
    noul: float | None = None
    choice: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None

class JevResponse(BaseModel):
    answers: dict[str, JevAnswer]
    input_tokens: int = 0

class JevError(Exception):
    pass

class Jev(Protocol):
    async def ask(self, model: str, state: str, questions: dict[str, dict]) -> JevResponse: ...

class OpenRouterJev:
    """
    Jev through OpenRouter's Decisions API (not chat completions).
    """

    def __init__(
        self,
        settings: "Settings",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ):
        self._url = settings.jev_url
        self._key = settings.openrouter_api_key
        self._transport = transport
        self._timeout = timeout

    async def ask(self, model: str, state: str, questions: dict[str, dict]) -> JevResponse:
        if self._key is None:
            raise JevError("OPENROUTER_API_KEY is not set")
        body = {"model": model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        try:
            client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
            async with client:
                response = await client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as error:
            raise JevError(f"Jev request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise JevError(f"Jev returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            raise JevError("Jev returned invalid JSON") from error
        return JevResponse(
            answers=_parse_answers(data.get("answers") or {}),
            input_tokens=(data.get("usage") or {}).get("input_tokens", 0),
        )

def _parse_answers(raw: dict) -> dict[str, JevAnswer]:
    answers = {}
    for key, value in raw.items():
        try:
            answers[key] = JevAnswer.model_validate(value)
        except ValidationError:
            continue
    return answers
