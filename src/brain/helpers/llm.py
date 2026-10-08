import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

import httpx
import openai
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from brain.deps import Settings

T = TypeVar("T", bound=BaseModel)

Message = dict[str, Any]

class ToolDef(BaseModel):
    name: str
    description: str
    parameters: dict

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class Completion(BaseModel):
    text: str | None
    tool_calls: list[ToolCall] = []
    tokens: int = 0

@dataclass
class Structured(Generic[T]):
    value: T
    tokens: int

class LLMError(Exception):
    pass

class LLM(Protocol):
    async def complete(
        self, model: str, messages: list[Message], tools: list[ToolDef] | None = None
    ) -> Completion: ...

    async def structured(
        self, model: str, messages: list[Message], schema: type[T]
    ) -> Structured[T]: ...

class OpenRouterLLM:
    """
    Chat models on OpenRouter through its OpenAI-compatible API.
    """

    def __init__(self, settings: "Settings", http_client: httpx.AsyncClient | None = None):
        key = settings.openrouter_api_key
        self._client = openai.AsyncOpenAI(
            api_key=key.get_secret_value() if key else "missing",
            base_url=settings.openrouter_base_url,
            max_retries=1,
            http_client=http_client,
        )

    async def complete(
        self, model: str, messages: list[Message], tools: list[ToolDef] | None = None
    ) -> Completion:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t.model_dump()} for t in tools]
        response = await self._create(model, messages, **kwargs)
        message = response.choices[0].message
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=_parse_arguments(call))
            for call in message.tool_calls or []
        ]
        return Completion(text=message.content, tool_calls=tool_calls, tokens=_tokens(response))

    async def structured(
        self, model: str, messages: list[Message], schema: type[T]
    ) -> Structured[T]:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        tokens = 0
        for _ in range(2):
            response = await self._create(model, messages, response_format=response_format)
            tokens += _tokens(response)
            content = response.choices[0].message.content or ""
            try:
                value = schema.model_validate_json(_strip_fences(content))
            except ValidationError as error:
                retry = f"That didn't match the schema: {error}. Reply with valid JSON only."
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": retry},
                ]
                continue
            return Structured(value=value, tokens=tokens)
        raise LLMError(f"{model} returned invalid {schema.__name__} twice")

    async def _create(self, model: str, messages: list[Message], **kwargs):
        try:
            response = await self._client.chat.completions.create(
                model=model, messages=messages, **kwargs
            )
        except openai.OpenAIError as error:
            raise LLMError(f"{model}: {type(error).__name__}") from error
        if not response.choices:
            raise LLMError(f"{model} returned no choices")
        return response

def _parse_arguments(call) -> dict:
    try:
        arguments = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        return {"_raw": call.function.arguments}
    return arguments if isinstance(arguments, dict) else {"_raw": arguments}

def _tokens(response) -> int:
    return response.usage.total_tokens if response.usage else 0

def _strip_fences(content: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    return match.group(1) if match else content
