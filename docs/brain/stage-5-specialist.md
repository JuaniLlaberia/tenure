# Stage 5: Specialist subgraph

**Status:** done
**Depends on:** stages 1, 2
**Spec:** [brain-engine.md](../specs/brain-engine.md) §2 (layers), §4 (specialist), §7 (prompt budget)

## Goal

One generic LangGraph subgraph that runs any specialist (writer, researcher, invoicer) from its template entry: an agent ⇄ tools loop with a step limit, then a final structured output. Plus the brain's read-only tools and the prompt-context builder that every role uses.

## Files

| File | Contains |
| --- | --- |
| `src/brain/tools.py` | `BrainTool`, `BRAIN_TOOLS` (`read_memory`, `web_search`, `fetch_page`), `tool_defs()` |
| `src/brain/context.py` | `build_context()`, the prompt budget constants |
| `src/brain/graphs/specialist.py` | `SpecialistState`, `build_specialist_graph()` |
| `src/brain/prompts/specialist.py` | the specialist's messages |

## Interfaces

```python
# tools.py
@dataclass
class ToolContext: deps: Deps; business_id: str; team_id: str; task_type: str
class BrainTool(BaseModel):
    name: str; description: str; parameters: dict          # JSON schema
    run: Callable[[ToolContext, dict], Awaitable[str]]      # returns text for the LLM
BRAIN_TOOLS: dict[str, BrainTool]                           # keys == registries.TOOL_NAMES
def tool_defs(names: list[str]) -> list[ToolDef]: ...

# context.py
MAX_LESSONS = 20
MAX_EXAMPLES = 3
MAX_FETCH_CHARS = 8_000
async def build_context(
    deps: Deps, business_id: str, team_id: str | None, task_type: str | None,
    prior_outputs: dict[str, dict] | None = None, feedback: list[str] | None = None,
) -> str: ...
    # sections, each left out when empty: Business (profile), What you know (lessons, newest first, max 20),
    # Approved examples (previews of approved/edited approvals of this task type, max 3; edited → edited_text),
    # Earlier steps (prior outputs by specialist id), Fix these (feedback)

# graphs/specialist.py
class SpecialistState(TypedDict):
    business_id: str; team_id: str; task_id: str; task_type: str
    specialist_id: str
    persona: Persona
    tool_names: list[str]
    output: str                 # registry name: the task type's output, or "notes" for earlier steps
    brief: str
    context: str                # from build_context
    max_steps: int
    messages: list[Message]
    steps: int
    tokens: int
    result: dict | None
def build_specialist_graph(deps: Deps) -> CompiledStateGraph: ...   # compiled once, no checkpointer
```

### Graph

```
START → agent ──tool calls and steps < max_steps──▶ tools ──▶ agent
          │ no tool calls, or steps == max_steps
          ▼
        finalize → END
```

- **agent:** `llm.complete(model_specialist, messages, tool_defs(tool_names))`; steps + 1; tokens added.
- **tools:** runs each tool call through `BRAIN_TOOLS`; unknown tool or exception → `"Tool error: …"` as the tool message. Emits `Progress` per call (`"Sam is searching: launch week ideas"`, `"Sam is reading example.com"`, `"Leo is checking what he knows"`).
- **finalize:** `llm.structured(model_specialist, messages + "Now give the final result", OUTPUTS[output].schema)` → `result = value.model_dump()`. A failed structured call raises out of the subgraph (the team graph turns it into a failed task).
- On start, one `Progress`: `"{name} is working on: {brief}"` (shortened to 80 characters).

### Tools

| Name | Arguments | Does |
| --- | --- | --- |
| `read_memory` | none | profile + active lessons for this team and task type, as text |
| `web_search` | `query: str` | `tools.web_search(query, k=5)` → numbered title / url / snippet |
| `fetch_page` | `url: str` | `tools.fetch_page(url)` → title + text, cut to 8,000 characters; `None` → "Couldn't open that page." |

There is no action tool, and none may be added here: actions run only at GATE.

## Tests

`tests/brain/test_context.py`

- `test_context_has_profile_and_lessons`
- `test_context_caps_lessons_at_twenty_newest_first`
- `test_context_examples_are_approved_drafts_max_three`: rejected ones excluded; an edited one shows the edited text.
- `test_context_includes_prior_outputs_and_feedback`
- `test_context_without_profile_still_works`

`tests/brain/test_brain_tools.py`

- `test_tool_names_match_registry`: `BRAIN_TOOLS.keys() == TOOL_NAMES`.
- `test_web_search_formats_results`
- `test_fetch_page_truncates_and_handles_none`
- `test_read_memory_reads_profile_and_lessons`
- `test_no_action_tools_exist`: no tool named or calling `post_social`, `send_email` or `delete_social`.

`tests/brain/test_specialist.py`

- `test_ends_with_result_in_output_schema`: no tool calls → finalize → `result` validates as `social_post`.
- `test_only_own_tools_are_offered`: the writer gets `read_memory` only; checked in `FakeLLM.calls`.
- `test_tool_calls_reach_fake_tools`: a scripted `web_search` call → `FakeTools.calls` has it → a tool message goes back to the LLM.
- `test_tool_error_goes_back_as_text`: an unknown tool name → `"Tool error"` in the next messages; no crash.
- `test_stops_at_max_steps`: the LLM always asks for a tool → exactly `max_steps` agent calls, then finalize.
- `test_emits_progress_with_persona`: collected from `astream(stream_mode="custom")`.
- `test_tokens_are_summed`
- `test_earlier_step_produces_notes`: `output="notes"` → result has `notes` and `sources`.
- `test_brief_context_and_feedback_reach_the_prompt`

## Implementation notes

- Tool results go back as OpenAI `{"role": "tool", "tool_call_id": ..., "content": ...}` messages after an assistant message carrying the `tool_calls`.
- The specialist's system prompt: persona, the task type's description, the brief, the context, "don't invent facts or prices", and the output's fields. It never mentions actions.
- Emitting events: `get_stream_writer()` inside nodes. A subgraph invoked from a node does **not** forward its custom events to the parent's stream (checked on LangGraph 1.2.14, with or without passing `config`). The team graph calls `run_specialist(graph, state)`, which streams the subgraph and re-emits each event through the parent's writer, then returns the final state.

## Done when

- [x] All tests above pass; earlier stages still pass
- [x] `ruff check` passes
- [x] Juan has reviewed the tests (Oct 9)

## Log

- Oct 8: implemented under `/goal implement stage 5`. Tests written first and run red (12 failed, 11 errors on stubs; 178 earlier tests passing), then implemented: 201 passed, `ruff check` clean.
- LangGraph finding: subgraph custom events don't reach the parent's stream, so the stage 6 plan to "pass `config` through" was wrong. Added `run_specialist()` (re-emits through the parent's writer); `subgraphs=True` also works but wraps every chunk in a namespace tuple.
- Tests added beyond the list above: `test_tool_defs_follow_the_names_given`, `test_web_search_without_results_says_so` (tools); `test_tool_exception_goes_back_as_text`, `test_run_specialist_re_emits_events_in_parent_stream` (specialist). `test_stops_at_max_steps` also checks that the final structured call has no unanswered tool calls.
- When the step limit is hit while the LLM still wants tools, `finalize` drops that last assistant message: the OpenAI format rejects tool calls without tool replies.
- A tool the specialist doesn't own counts as unknown ("Tool error: there is no tool called …"), even if it exists for another specialist.
- `context.py` exposes its sections (`profile_section`, `lessons_section`, `examples_section`, `prior_section`, `feedback_section`); `read_memory` reuses the first two. Examples are the 3 newest approved or edited drafts (edited ones show the founder's text), read from `recent_approvals(limit=10)`.
- Manual live run (scratch script, real DeepSeek, fake tools): the researcher made 5 tool calls over 4 steps, hit the limit, and returned valid `notes` in 5,042 tokens. The tool-call message format works on OpenRouter.
