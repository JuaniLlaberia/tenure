import json
import logging
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from brain.deps import Deps
from brain.helpers.llm import Message
from brain.prompts.specialist import final_message, specialist_messages
from brain.templates.registries import OUTPUTS
from brain.tools import BRAIN_TOOLS, ToolContext, tool_defs
from contract import Persona, Progress

log = logging.getLogger(__name__)

BRIEF_PREVIEW = 80

class SpecialistState(TypedDict, total=False):
    business_id: str
    team_id: str
    task_id: str
    task_type: str
    specialist_id: str
    persona: Persona
    tool_names: list[str]
    output: str
    brief: str
    context: str
    max_steps: int
    messages: list[Message]
    pending: list[dict]
    steps: int
    tokens: int
    result: dict | None

def build_specialist_graph(deps: Deps) -> CompiledStateGraph:
    """
    One generic specialist: agent ⇄ tools up to max_steps, then a final structured output.
    Compiled once and reused for every specialist of every team.
    """

    def progress(state: SpecialistState, status: str) -> None:
        get_stream_writer()(
            Progress(
                team_id=state["team_id"],
                task_id=state["task_id"],
                persona=state["persona"],
                status=status,
            )
        )

    async def start(state: SpecialistState) -> dict:
        brief = state["brief"]
        short = brief if len(brief) <= BRIEF_PREVIEW else brief[: BRIEF_PREVIEW - 1] + "…"
        progress(state, f"{state['persona'].name} is working on: {short}")
        messages = specialist_messages(
            state["persona"], state["task_type"], state["output"], brief, state.get("context", "")
        )
        return {"messages": messages, "pending": [], "steps": 0, "tokens": 0, "result": None}

    async def agent(state: SpecialistState) -> dict:
        completion = await deps.llm.complete(
            deps.settings.model_specialist,
            state["messages"],
            tool_defs(state["tool_names"]) or None,
        )
        message: Message = {"role": "assistant", "content": completion.text or ""}
        pending = [call.model_dump() for call in completion.tool_calls]
        if pending:
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                }
                for call in pending
            ]
        return {
            "messages": [*state["messages"], message],
            "pending": pending,
            "steps": state["steps"] + 1,
            "tokens": state["tokens"] + completion.tokens,
        }

    async def tools(state: SpecialistState) -> dict:
        ctx = ToolContext(
            deps=deps,
            business_id=state["business_id"],
            team_id=state["team_id"],
            task_type=state["task_type"],
        )
        replies = []
        for call in state["pending"]:
            content = await _run(state, ctx, call)
            replies.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        return {"messages": [*state["messages"], *replies], "pending": []}

    async def _run(state: SpecialistState, ctx: ToolContext, call: dict) -> str:
        tool = BRAIN_TOOLS.get(call["name"])
        if tool is None or call["name"] not in state["tool_names"]:
            return f"Tool error: there is no tool called {call['name']!r}."
        progress(state, tool.status(state["persona"].name, call["arguments"]))
        try:
            return await tool.run(ctx, call["arguments"])
        except Exception as error:
            log.warning("Tool %s failed: %s", call["name"], error)
            return f"Tool error: {call['name']} failed ({type(error).__name__})."

    async def finalize(state: SpecialistState) -> dict:
        messages = list(state["messages"])
        if state.get("pending"):
            messages = messages[:-1]
        schema = OUTPUTS[state["output"]].schema
        result = await deps.llm.structured(
            deps.settings.model_specialist, [*messages, final_message(schema)], schema
        )
        return {"result": result.value.model_dump(), "tokens": state["tokens"] + result.tokens}

    def after_agent(state: SpecialistState) -> str:
        if state["pending"] and state["steps"] < state["max_steps"]:
            return "tools"
        return "finalize"

    graph = StateGraph(SpecialistState)
    graph.add_node("start", start)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "start")
    graph.add_edge("start", "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "finalize"])
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile()

async def run_specialist(graph: CompiledStateGraph, state: SpecialistState) -> SpecialistState:
    """
    Runs the specialist from inside a team-graph node. A subgraph's custom events don't reach
    the parent's stream on their own, so this re-emits them through the parent's writer.
    """
    writer = get_stream_writer()
    final: SpecialistState = state
    async for mode, chunk in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            writer(chunk)
        else:
            final = chunk
    return final
