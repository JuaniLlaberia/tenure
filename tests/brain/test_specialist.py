from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from brain.graphs.specialist import build_specialist_graph, run_specialist
from brain.helpers.llm import Completion, ToolCall
from brain.templates.registries import NotesOutput, SocialPostOutput
from contract import Persona, Progress, SearchResult

LEO = Persona(name="Leo", role="Writer")
SAM = Persona(name="Sam", role="Researcher")

def specialist_input(**overrides):
    state = {
        "business_id": "b1",
        "team_id": "t1",
        "task_id": "k1",
        "task_type": "social_post",
        "specialist_id": "writer",
        "persona": LEO,
        "tool_names": ["read_memory"],
        "output": "social_post",
        "brief": "Announce our Friday launch",
        "context": "Business: Juan's Studio",
        "max_steps": 6,
    }
    state.update(overrides)
    return state

def search_call(query="launch week", call_id="c1"):
    return ToolCall(id=call_id, name="web_search", arguments={"query": query})

async def run(graph, state):
    events, final = [], None
    async for mode, chunk in graph.astream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(chunk)
        else:
            final = chunk
    return events, final

def prompt_text(call):
    return "\n".join(str(message.get("content", "")) for message in call.messages)

@pytest.fixture
def graph(deps):
    return build_specialist_graph(deps)

async def test_ends_with_result_in_output_schema(graph, llm):
    llm.structured_responses["SocialPostOutput"] = {"text": "We launch Friday!"}

    _, final = await run(graph, specialist_input())

    assert SocialPostOutput.model_validate(final["result"]) == SocialPostOutput(
        text="We launch Friday!"
    )
    kinds = [call.kind for call in llm.calls]
    assert kinds == ["complete", "structured"]
    assert llm.calls[-1].schema is SocialPostOutput

async def test_only_own_tools_are_offered(graph, llm):
    llm.structured_responses["SocialPostOutput"] = {"text": "Hi"}

    await run(graph, specialist_input(tool_names=["read_memory"]))

    assert [tool.name for tool in llm.calls[0].tools] == ["read_memory"]

async def test_tool_calls_reach_fake_tools(graph, llm, tools):
    tools.search_results = [SearchResult(title="Launch tips", url="https://a.co", snippet="Early")]
    llm.completions = [Completion(text=None, tool_calls=[search_call()]), Completion(text="Done")]
    llm.structured_responses["NotesOutput"] = {"notes": "Post early", "sources": ["https://a.co"]}

    await run(
        graph,
        specialist_input(
            specialist_id="researcher",
            persona=SAM,
            tool_names=["web_search", "fetch_page"],
            output="notes",
        ),
    )

    assert ("web_search", {"query": "launch week", "k": 5}) in tools.calls
    second = llm.calls[1].messages
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second)
    tool_messages = [m for m in second if m.get("role") == "tool"]
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "Launch tips" in tool_messages[0]["content"]

async def test_tool_error_goes_back_as_text(graph, llm):
    bad = ToolCall(id="c1", name="post_social", arguments={"text": "Hi"})
    llm.completions = [Completion(text=None, tool_calls=[bad]), Completion(text="Done")]
    llm.structured_responses["SocialPostOutput"] = {"text": "Hi"}

    _, final = await run(graph, specialist_input())

    tool_messages = [m for m in llm.calls[1].messages if m.get("role") == "tool"]
    assert "Tool error" in tool_messages[0]["content"]
    assert final["result"] == {"text": "Hi"}

async def test_tool_exception_goes_back_as_text(graph, deps, llm):
    class BrokenTools(type(deps.tools)):
        async def web_search(self, query, k=5):
            raise RuntimeError("search is down")

    deps.tools = BrokenTools()
    llm.completions = [Completion(text=None, tool_calls=[search_call()]), Completion(text="Done")]
    llm.structured_responses["NotesOutput"] = {"notes": "Nothing found", "sources": []}

    await run(
        graph,
        specialist_input(persona=SAM, tool_names=["web_search"], output="notes"),
    )

    tool_messages = [m for m in llm.calls[1].messages if m.get("role") == "tool"]
    assert "Tool error" in tool_messages[0]["content"]

async def test_stops_at_max_steps(graph, llm):
    counter = iter(range(100))
    llm.completions = lambda messages: Completion(
        text=None, tool_calls=[search_call(call_id=f"c{next(counter)}")]
    )
    llm.structured_responses["NotesOutput"] = {"notes": "Enough", "sources": []}

    _, final = await run(
        graph,
        specialist_input(persona=SAM, tool_names=["web_search"], output="notes", max_steps=3),
    )

    assert [call.kind for call in llm.calls] == ["complete"] * 3 + ["structured"]
    assert final["steps"] == 3
    assert final["result"] == {"notes": "Enough", "sources": []}
    final_messages = llm.calls[-1].messages
    tool_call_ids = {
        call["id"] for m in final_messages for call in m.get("tool_calls") or []
    }
    answered = {m["tool_call_id"] for m in final_messages if m.get("role") == "tool"}
    assert tool_call_ids <= answered

async def test_emits_progress_with_persona(graph, llm, tools):
    llm.completions = [Completion(text=None, tool_calls=[search_call()]), Completion(text="Done")]
    llm.structured_responses["NotesOutput"] = {"notes": "Post early", "sources": []}

    events, _ = await run(
        graph,
        specialist_input(persona=SAM, tool_names=["web_search"], output="notes"),
    )

    assert events
    assert all(isinstance(e, Progress) for e in events)
    assert all(e.persona == SAM and e.task_id == "k1" and e.team_id == "t1" for e in events)
    assert events[0].status.startswith("Sam")
    assert any("launch week" in e.status for e in events)

async def test_tokens_are_summed(graph, llm):
    llm.tokens_per_call = 10
    llm.completions = [Completion(text=None, tool_calls=[search_call()]), Completion(text="Done")]
    llm.structured_responses["NotesOutput"] = {"notes": "Post early", "sources": []}

    _, final = await run(
        graph,
        specialist_input(persona=SAM, tool_names=["web_search"], output="notes"),
    )

    assert final["tokens"] == 30

async def test_earlier_step_produces_notes(graph, llm):
    llm.structured_responses["NotesOutput"] = {"notes": "Rivals post on Mondays", "sources": []}

    _, final = await run(graph, specialist_input(output="notes"))

    assert NotesOutput.model_validate(final["result"]).notes == "Rivals post on Mondays"

async def test_brief_context_and_feedback_reach_the_prompt(graph, llm):
    llm.structured_responses["SocialPostOutput"] = {"text": "Hi"}

    await run(
        graph,
        specialist_input(
            brief="Announce our Friday launch",
            context="Business: Juan's Studio\n\nFix these:\n- Mention the date",
        ),
    )

    prompt = prompt_text(llm.calls[0])
    for text in ["Announce our Friday launch", "Juan's Studio", "Mention the date", "Leo"]:
        assert text in prompt
    assert "post_social" not in prompt

async def test_run_specialist_re_emits_events_in_parent_stream(graph, llm):
    llm.structured_responses["SocialPostOutput"] = {"text": "We launch Friday!"}

    class Parent(TypedDict, total=False):
        result: dict

    async def node(state):
        final = await run_specialist(graph, specialist_input())
        return {"result": final["result"]}

    builder = StateGraph(Parent)
    builder.add_node("node", node)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)
    parent = builder.compile()

    chunks = [c async for c in parent.astream({}, stream_mode=["custom", "updates"])]

    custom = [chunk for mode, chunk in chunks if mode == "custom"]
    assert custom and all(isinstance(e, Progress) for e in custom)
    updates = [chunk for mode, chunk in chunks if mode == "updates"]
    assert updates[-1]["node"]["result"] == {"text": "We launch Friday!"}
