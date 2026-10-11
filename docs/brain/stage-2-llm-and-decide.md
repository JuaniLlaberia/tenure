# Stage 2: LLM layer and `decide()`

**Status:** done
**Depends on:** stage 0 (stage 1 for the `deps` fixture)
**Spec:** [brain-engine.md](../specs/brain-engine.md) §9 (`decide()`, Jev on OpenRouter, models)

## Goal

One way to call LLMs (plain completions with tools, and structured output), one way to make judgement calls (`decide()`, Jev first, LLM fallback), the settings that configure them, and fakes for both so no other test touches the network.

## Files

| File | Contains |
| --- | --- |
| `src/brain/deps.py` | `Settings`, `Deps` |
| `src/brain/helpers/llm.py` | `LLM` protocol, `OpenRouterLLM`, message and tool types, `LLMError` |
| `src/brain/helpers/jev.py` | `Jev` protocol, `OpenRouterJev`, `JevAnswer`, `JevResponse`, `JevError` |
| `src/brain/helpers/decide.py` | `Question`, `Decision`, `Decisions`, `decide()` |
| `src/brain/fakes.py` | adds `FakeLLM`, `FakeJev` |
| `tests/brain/conftest.py` | `settings`, `store`, `tools`, `llm`, `jev`, `clock`, `deps` fixtures |

## Interfaces

```python
# deps.py
class Settings(BaseModel):
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    jev_url: str = "https://openrouter.ai/api/alpha/decisions"
    model_lead: str = "deepseek/deepseek-v4-flash-0731"
    model_specialist: str = "deepseek/deepseek-v4-flash-0731"
    model_reflect: str = "deepseek/deepseek-v4-flash-0731"
    model_decide_fallback: str = "deepseek/deepseek-v4-flash-0731"
    model_jev: str = "typesafe/jev-1.13"
    decide_threshold: float = 0.7
    promotion_confidence: float = 0.8
    database_url: SecretStr | None = None
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings": ...
        # env=None → load .env (python-dotenv) then os.environ; empty strings mean "use the default"

@dataclass
class Deps:
    store: Store
    tools: Tools
    llm: LLM
    jev: Jev
    settings: Settings
    templates: dict[str, Template]
    clock: Callable[[], datetime] = utcnow

# helpers/llm.py
Message = dict[str, Any]                       # OpenAI chat format
class ToolDef(BaseModel):    name: str; description: str; parameters: dict   # JSON schema
class ToolCall(BaseModel):   id: str; name: str; arguments: dict
class Completion(BaseModel): text: str | None; tool_calls: list[ToolCall] = []; tokens: int = 0
@dataclass
class Structured(Generic[T]): value: T; tokens: int
class LLMError(Exception): ...
class LLM(Protocol):
    async def complete(self, model: str, messages: list[Message], tools: list[ToolDef] | None = None) -> Completion: ...
    async def structured(self, model: str, messages: list[Message], schema: type[T]) -> Structured[T]: ...
class OpenRouterLLM:          # openai.AsyncOpenAI(base_url=openrouter_base_url, max_retries=1)
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None): ...

# helpers/decide.py
class Question(BaseModel):
    instructions: str
    options: dict[str, str]                     # option → meaning; at least 2; SAFE OPTION LAST
    @classmethod
    def yes_no(cls, instructions: str, yes: str, no: str) -> "Question": ...   # options {"yes": ..., "no": ...}
    @property
    def safe(self) -> str: ...                  # the last option
class Decision(BaseModel):
    choice: str
    confidence: float                           # 0..1
    source: Literal["jev", "llm", "default"]
    def accepts(self, option: str, threshold: float) -> bool: ...   # choice == option and confidence >= threshold
class Decisions(BaseModel):
    answers: dict[str, Decision]
    tokens: int = 0
    def __getitem__(self, key: str) -> Decision: ...
MAX_STATE_CHARS = 60_000      # ≈ 15k tokens, well under Jev's 32k prompt limit
async def decide(deps: Deps, questions: dict[str, Question], state: str) -> Decisions: ...

# helpers/jev.py
class JevAnswer(BaseModel):
    type: Literal["noul", "choice", "score"]
    noul: float | None = None
    choice: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
class JevResponse(BaseModel): answers: dict[str, JevAnswer]; input_tokens: int = 0
class JevError(Exception): ...
class Jev(Protocol):
    async def ask(self, model: str, state: str, questions: dict[str, dict]) -> JevResponse: ...   # raises JevError
class OpenRouterJev:          # httpx.AsyncClient, POST settings.jev_url, 20 s timeout
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0): ...

# fakes.py
class FakeLLM:
    def __init__(
        self,
        structured: dict[str, Any] | None = None,
            # schema class name → a response, a list of responses (used in order, the last one repeats)
            #   or a callable(messages) -> response; a response is a model instance or a dict
        completions: list[Completion] | Callable[[list[Message]], Completion] | None = None,
            # default: Completion(text="Done.", tool_calls=[])
        fail: bool = False,                     # every call raises LLMError
        tokens_per_call: int = 10,
    ): ...
    calls: list[LLMCall]                        # kind ("complete" | "structured"), model, messages, tools, schema
class FakeJev:
    def __init__(self, answers: dict[str, Any] | None = None, fail: bool = False): ...
        # question key → float (noul), str (choice, confidence 0.95), dict / JevAnswer,
        #   or a callable(state) returning one of those; a missing key is left out of the response
    calls: list[tuple[str, str, dict]]          # (model, state, questions)
```

How `decide()` maps questions and answers (spec §9):

| Question | Sent as | Answer → `Decision` |
| --- | --- | --- |
| 2 options | `noul`, `criteria = {"true": first option's meaning, "false": second's}` | `choice = first if noul ≥ 0.5 else second`, `confidence = max(noul, 1 - noul)` |
| 3+ options | `choice`, `criteria = options` | `choice` as given, `confidence` as given (or the top probability if missing) |

Order of work in `decide()`:

1. Truncate `state` to the last `MAX_STATE_CHARS` characters, prefixed with `…` if cut.
2. One `jev.ask()` with every question. Keep the valid answers (`source="jev"`).
3. Questions that failed (Jev raised, the key is missing, or the choice isn't an option) go to one `llm.structured()` call on `model_decide_fallback` (`source="llm"`).
4. Still invalid, or the fallback raised → `Decision(choice=question.safe, confidence=0, source="default")`.
5. Clamp every confidence to 0..1. `tokens` = Jev's `input_tokens` + the fallback's tokens.

`decide()` never raises.

## Tests

`tests/brain/test_settings.py`

- `test_defaults`: models default to `deepseek/deepseek-v4-flash-0731` and `typesafe/jev-1.13`; threshold 0.7.
- `test_reads_env`: values from the mapping override the defaults; `DECIDE_THRESHOLD` parses as a float.
- `test_empty_env_values_use_defaults`: `MODEL_LEAD=""` keeps the default.
- `test_keys_hidden_in_repr`: the api key and database url never appear in `repr(settings)` or `str(settings)`.

`tests/brain/test_decide.py`

- `test_two_options_sent_as_noul`: the request to `FakeJev` has `type="noul"` and `criteria` with `true` / `false`.
- `test_three_options_sent_as_choice`
- `test_all_questions_in_one_jev_call`
- `test_noul_maps_to_first_option_above_half`: noul 0.97 → `yes`, confidence 0.97.
- `test_noul_maps_to_second_option_below_half`: noul 0.2 → `no`, confidence 0.8.
- `test_choice_uses_jev_confidence`
- `test_jev_error_falls_back_to_llm`: `FakeJev(fail=True)` → answers come from `FakeLLM`, `source="llm"`.
- `test_missing_answer_falls_back_for_that_question_only`: Jev answers one of two questions; only the other goes to the fallback.
- `test_invalid_choice_falls_back`: Jev returns a choice not in the options.
- `test_both_fail_returns_safe_option`: Jev and the LLM both fail → the last option, confidence 0, `source="default"`; no exception.
- `test_confidence_is_clamped`
- `test_accepts_needs_option_and_threshold`: `accepts("yes", 0.7)` is false for `yes` at 0.6 and for `no` at 0.9.
- `test_long_state_is_truncated_keeping_the_end`
- `test_tokens_add_jev_and_fallback`

`tests/brain/test_fakes_llm.py`

- `test_structured_responses_by_schema_name_in_order`: a list is used in order; the last one repeats.
- `test_structured_accepts_dicts_and_callables`
- `test_completions_default_to_no_tool_calls`
- `test_calls_are_recorded`: kind, model, messages and schema / tools.
- `test_fail_raises_llm_error`

`tests/brain/test_live.py` (marked `live`, skipped by default)

- `test_live_jev_decide`: "Stop using hashtags. Also post about our Friday launch." → has feedback `yes`, `source="jev"`.
- `test_live_structured_output`: a small schema comes back valid from `deepseek/deepseek-v4-flash-0731`.

## Implementation notes

- `OpenRouterLLM.structured()` sends `response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": schema.model_json_schema()}}` (not strict) and validates with `schema.model_validate_json`. On a validation error it retries once with the error appended to the messages, then raises `LLMError`.
- `OpenRouterLLM` wraps every `openai` exception in `LLMError`; tokens come from `usage.total_tokens`.
- `OpenRouterJev` request body: `{"model", "state", "questions"}`. Any non-200, timeout or bad JSON → `JevError`. The response's `usage.input_tokens` becomes `input_tokens`.
- The fallback prompt lists each question with its options and asks for `{"answers": {key: {"choice", "confidence"}}}`; the schema is the fixed model `DecideFallback` (`answers: dict[str, FallbackAnswer]`), so `FakeLLM` scripts it as `structured={"DecideFallback": ...}`.
- Keys are never logged; `Settings` uses `SecretStr`.

## Done when

- [x] All tests above pass (live ones skipped); earlier stages still pass
- [x] `uv run pytest -m live` passes with a real key
- [x] `ruff check` passes
- [x] Juan has reviewed the tests (Oct 8)

## Log

- Oct 8: Jev tested by hand. `POST https://openrouter.ai/api/alpha/decisions` works; `/api/v1/api/alpha/decisions` (shown in OpenRouter's docs) returns 404. Response shape: `{"model", "answers": {key: {"type": "noul", "noul": 0.97} | {"type": "choice", "choice", "probabilities", "confidence"}}, "usage": {"input_tokens", "output_tokens", "cost"}, "id", "provider"}`.
- Oct 8: layout decided by Juan: the `helpers/` approach. LLM client in `helpers/llm.py`, Jev client in `helpers/jev.py`, `decide()` in `helpers/decide.py` (new file: it uses both clients); the facade will live in `brain.py` (stage 6). Spec §11 and stage docs 6–8 updated.
- Oct 8: implemented under `/goal implement stage 2`. Tests written first and run red (22 failed, `test_decide.py` failed at collection), then implemented: 108 passed, 2 live deselected; `uv run pytest -m live` 2 passed against OpenRouter (Jev + DeepSeek structured output); `ruff check` clean. The tests were not reviewed before implementing; they are frozen from now on, and Juan's review is pending.
- Tests added beyond the list above: `test_question_needs_two_options`, `test_safe_is_last_option`, `test_invalid_fallback_choice_returns_safe_option`, `test_short_state_is_sent_as_is` (decide); `test_missing_structured_response_raises_llm_error`, `test_completions_in_order`, `test_fake_jev_shorthands`, `test_fake_jev_fail_raises` (fakes); and `tests/brain/test_openrouter_clients.py` (9 tests: request / response shapes of both real clients through `httpx.MockTransport`, no network).
- `FakeJev` also takes a list per key (used in order, the last repeats) and an `input_tokens` attribute; `FakeLLM.structured_responses` and `FakeJev.answers` are public so tests can script them after the fixtures are built.
- `OpenRouterLLM` takes an optional `http_client` and `OpenRouterJev` an optional `transport`, only for tests.
