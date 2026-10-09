import json
import re
from dataclasses import dataclass, field
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

class Usage(BaseModel):
    """
    What one call (or a structured call with its retry) used. `cost` is what OpenRouter billed.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None

    def __add__(self, other: "Usage") -> "Usage":
        costs = [c for c in (self.cost, other.cost) if c is not None]
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost=sum(costs) if costs else None,
        )

class Completion(BaseModel):
    text: str | None
    tool_calls: list[ToolCall] = []
    tokens: int = 0
    usage: Usage = Usage()

@dataclass
class Structured(Generic[T]):
    value: T
    tokens: int
    usage: Usage = field(default_factory=Usage)

class LLMError(Exception):
    pass

class LLM(Protocol):
    """
    `reasoning` turns the model's thinking on or off for one call; None leaves it to the model.
    """

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        reasoning: bool | None = None,
    ) -> Completion: ...

    async def structured(
        self, model: str, messages: list[Message], schema: type[T], reasoning: bool | None = None
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
            timeout=settings.llm_timeout,
            http_client=http_client,
        )
        self._allow_reasoning = settings.llm_reasoning
        self._extra: dict[str, Any] = {}
        if settings.openrouter_provider:
            self._extra["provider"] = {
                "order": [settings.openrouter_provider],
                "allow_fallbacks": True,
            }

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        reasoning: bool | None = None,
    ) -> Completion:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t.model_dump()} for t in tools]
        response = await self._create(model, messages, reasoning, **kwargs)
        message = response.choices[0].message
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=_parse_arguments(call))
            for call in message.tool_calls or []
        ]
        return Completion(
            text=message.content,
            tool_calls=tool_calls,
            tokens=_tokens(response),
            usage=_usage(response),
        )

    async def structured(
        self, model: str, messages: list[Message], schema: type[T], reasoning: bool | None = None
    ) -> Structured[T]:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        tokens, usage = 0, Usage()
        for _ in range(2):
            response = await self._create(
                model, messages, reasoning, response_format=response_format
            )
            tokens += _tokens(response)
            usage += _usage(response)
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
            return Structured(value=value, tokens=tokens, usage=usage)
        raise LLMError(f"{model} returned invalid {schema.__name__} twice")

    def _body(self, reasoning: bool | None) -> dict[str, Any] | None:
        """
        LLM_REASONING=false turns off every call that asks for reasoning. None leaves it to the
        model and sends nothing (some models, like Claude Sonnet, can't turn it off).
        """
        if not self._allow_reasoning and reasoning:
            reasoning = False
        if reasoning is None:
            return self._extra or None
        return {**self._extra, "reasoning": {"enabled": reasoning}}

    async def _create(
        self, model: str, messages: list[Message], reasoning: bool | None, **kwargs
    ):
        try:
            response = await self._client.chat.completions.create(
                model=model, messages=messages, extra_body=self._body(reasoning), **kwargs
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

def _usage(response) -> Usage:
    usage = response.usage
    if usage is None:
        return Usage()
    cost = (usage.model_extra or {}).get("cost")
    return Usage(
        input_tokens=usage.prompt_tokens or 0,
        output_tokens=usage.completion_tokens or 0,
        cost=float(cost) if cost is not None else None,
    )

def _strip_fences(content: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    return match.group(1) if match else content
