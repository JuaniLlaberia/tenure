"""
Logs one ModelUsage row per LLM or Jev call. Who the call worked for comes from a context
variable: the Brain sets the business and team at each entry point, team nodes add the task.
LangGraph runs each node in a task that copies the context, so a node's scope stays its own.
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass, replace

from brain.common import new_id
from brain.deps import Settings
from brain.helpers.jev import Jev, JevResponse
from brain.helpers.llm import LLM, Completion, Message, Structured, T, ToolDef, Usage
from contract import ModelUsage, Store

log = logging.getLogger(__name__)

ROLES = {
    "model_lead": "Planning and chat",
    "model_specialist": "Writing and research",
    "model_reflect": "Learning",
    "model_decide_fallback": "Decision backup",
    "model_critic": "Reviews",
}

@dataclass(frozen=True)
class Scope:
    business_id: str
    team_id: str | None = None
    task_id: str | None = None

_scope: ContextVar[Scope | None] = ContextVar("usage_scope", default=None)

def enter(business_id: str, team_id: str | None = None) -> None:
    """
    Sets who the next calls work for, at a Brain entry point.
    """
    _scope.set(Scope(business_id, team_id))

def enter_task(task_id: str) -> None:
    current = _scope.get()
    if current is not None:
        _scope.set(replace(current, task_id=task_id))

def purposes(settings: Settings) -> dict[str, str]:
    """
    Model id → what it's used for, in plain words ("Planning and chat, Learning").
    """
    found: dict[str, list[str]] = {}
    for field, label in ROLES.items():
        found.setdefault(getattr(settings, field), []).append(label)
    found.setdefault(settings.model_jev, []).append("Decisions")
    return {model: ", ".join(labels) for model, labels in found.items()}

class UsageLog:
    def __init__(self, store: Store, settings: Settings, clock) -> None:
        self._store = store
        self._purposes = purposes(settings)
        self._clock = clock

    async def record(self, model: str, usage: Usage) -> None:
        scope = _scope.get()
        if scope is None:
            return
        try:
            await self._store.log_usage(
                ModelUsage(
                    usage_id=new_id(),
                    business_id=scope.business_id,
                    team_id=scope.team_id,
                    task_id=scope.task_id,
                    model=model,
                    purpose=self._purposes.get(model),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost=usage.cost,
                    at=self._clock(),
                )
            )
        except Exception as error:
            log.warning("Couldn't log usage for %s: %s", model, error)

class MeteredLLM:
    def __init__(self, llm: LLM, usage: UsageLog) -> None:
        self._llm = llm
        self._usage = usage

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        reasoning: bool | None = None,
    ) -> Completion:
        completion = await self._llm.complete(model, messages, tools, reasoning=reasoning)
        await self._usage.record(model, completion.usage)
        return completion

    async def structured(
        self, model: str, messages: list[Message], schema: type[T], reasoning: bool | None = None
    ) -> Structured[T]:
        result = await self._llm.structured(model, messages, schema, reasoning=reasoning)
        await self._usage.record(model, result.usage)
        return result

class MeteredJev:
    def __init__(self, jev: Jev, usage: UsageLog) -> None:
        self._jev = jev
        self._usage = usage

    async def ask(self, model: str, state: str, questions: dict[str, dict]) -> JevResponse:
        response = await self._jev.ask(model, state, questions)
        usage = Usage(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost=response.cost,
        )
        await self._usage.record(model, usage)
        return response
