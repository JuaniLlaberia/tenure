# Stage 1: Fakes and templates

**Status:** done
**Depends on:** stage 0
**Spec:** [brain-engine.md](../specs/brain-engine.md) §8 (templates); [CONTRACT.md](../CONTRACT.md) §3 (conventions), §9 (tools), §10 (store)

## Goal

An in-memory `Store` and fake `Tools` that behave like Mark's real ones, and team templates loaded from YAML into typed, validated models.

## Files

| File | Contains |
| --- | --- |
| `src/brain/common.py` | `new_id()`, `utcnow()` |
| `src/brain/fakes.py` | `InMemoryStore`, `FakeTools` (`FakeLLM` and `FakeJev` come in stage 2) |
| `src/brain/templates/models.py` | `Template` and its parts |
| `src/brain/templates/registries.py` | output types, action names, tool names |
| `src/brain/templates/loader.py` | `load_template`, `load_templates`, `template_info`, `TemplateError` |
| `src/brain/templates/marketing.yaml` | the marketing team, exactly as spec §8 |

## Interfaces

```python
# common.py
def new_id() -> str: ...            # uuid4 string
def utcnow() -> datetime: ...       # timezone-aware UTC

# fakes.py
class InMemoryStore:                # satisfies contract.Store
    ...                             # every Store method; stores deep copies

class FakeTools:                    # satisfies contract.Tools
    def __init__(
        self,
        search_results: list[SearchResult] | None = None,
        pages: dict[str, PageContent] | None = None,
        echo: bool = False,         # print actions (for the CLI)
    ): ...
    calls: list[tuple[str, dict]]   # (method name, kwargs), in order
    fail: set[str]                  # method names that return ok=False / [] / None

# templates/models.py
class LeadSpec(BaseModel):          persona: Persona; instructions: str
class SpecialistSpec(BaseModel):    persona: Persona; tools: list[str] = []
class TaskTypeSpec(BaseModel):
    description: str
    steps: list[str]                # min 1
    output: str
    check: list[str] = []
    action: str | None = None
    start_level: AutonomyLevel = AutonomyLevel.ACT_AFTER_APPROVAL
    max_level: AutonomyLevel | None = None   # None → same as start_level (never promoted)
class Limits(BaseModel):            max_tasks_per_request: int = 3; max_steps_per_specialist: int = 6; token_budget: int = 60000
class Template(BaseModel):
    name: str; display_name: str; description: str
    lead: LeadSpec
    specialists: dict[str, SpecialistSpec]
    task_types: dict[str, TaskTypeSpec]
    onboarding: dict[str, str] = {}          # key → question, max 3
    limits: Limits = Limits()
    def max_level(self, task_type: str) -> AutonomyLevel: ...
    def personas(self) -> list[Persona]: ... # lead first, then specialists in YAML order

# templates/registries.py
class OutputType:
    name: str
    schema: type[BaseModel]
    def preview(self, output: BaseModel) -> str: ...
    def to_planned_action(self, output: BaseModel, action: str | None) -> PlannedAction | None: ...
OUTPUTS: dict[str, OutputType]       # social_post, email, report, notes
ACTIONS: dict[str, str]              # action name → output it needs: post_social → social_post, send_email → email
TOOL_NAMES: frozenset[str]           # read_memory, web_search, fetch_page (implemented in stage 5)

# templates/loader.py
class TemplateError(ValueError): ...
def load_template(path: Path) -> Template: ...
def load_templates(directory: Path = TEMPLATES_DIR) -> dict[str, Template]: ...   # name → Template
def template_info(template: Template) -> TemplateInfo: ...
```

Output schemas:

| Output | Fields | `preview()` | Planned action |
| --- | --- | --- | --- |
| `social_post` | `text` | the text | `PostSocial(text)` for `post_social` |
| `email` | `to`, `subject`, `body` | `To: …\nSubject: …\n\n…body` | `SendEmail(...)` for `send_email` |
| `report` | `summary`, `sources: list[str]` | summary + bullet list of sources | `None` |
| `notes` | `notes`, `sources: list[str]` | notes | `None` (only for earlier steps) |

## Tests

`tests/brain/test_store_fake.py`

- `test_get_returns_none_when_missing`: every `get_*` returns `None` for an unknown id.
- `test_save_is_an_upsert`: saving the same id twice keeps one record, the latest.
- `test_returned_records_are_copies`: mutating a returned model doesn't change the stored one.
- `test_list_teams_filters_by_business`
- `test_recent_approvals_only_resolved_newest_first`: pending approvals are excluded; order by `resolved_at` descending; `limit` respected; filtered by team and task type.
- `test_list_lessons_business_wide_plus_team`: returns business-wide lessons plus the given team's, not other teams'.
- `test_list_lessons_filters_by_task_type`: keeps lessons for that task type and for any (`None`).
- `test_list_lessons_only_active_newest_first`
- `test_trust_keyed_by_team_and_task_type`
- `test_audit_log_round_trip`: `log_action` then `get_action`; logging again with the same id updates it.

`tests/brain/test_tools_fake.py`

- `test_actions_are_recorded`: each call appears in `calls` with its arguments.
- `test_post_social_returns_url_and_external_id`: `ok=True`, a fresh `action_id`, a url and an external id.
- `test_fail_makes_methods_fail_softly`: with a method in `fail`, actions return `ok=False` with `error`, search returns `[]`, fetch returns `None`; nothing raises.
- `test_search_and_fetch_return_configured_data`: unknown url → `None`.

`tests/brain/test_templates.py`

- `test_marketing_template_loads`: three task types, two specialists, three onboarding questions, limits as in the YAML.
- `test_max_level_defaults_to_start_level`: `competitor_check` has no `max_level`.
- `test_personas_lead_first`
- `test_template_info`: `TemplateInfo` has name, display name, description, personas (lead first) and task types.
- `test_load_templates_keys_by_name`
- Fails fast with `TemplateError` (one test each, from a YAML written to `tmp_path`):
  - `test_unknown_specialist_in_steps`
  - `test_unknown_tool`
  - `test_unknown_output`
  - `test_unknown_action`
  - `test_action_output_mismatch` (`post_social` with an `email` output)
  - `test_start_level_above_max_level`
  - `test_more_than_three_onboarding_questions`
  - `test_empty_steps`

`tests/brain/test_registries.py`

- `test_social_post_maps_to_post_social`
- `test_email_maps_to_send_email`
- `test_report_has_no_planned_action`
- `test_previews_show_what_will_be_sent`: the email preview contains to, subject and body.
- `test_outputs_validate_their_schema`: missing fields raise `ValidationError`.

## Implementation notes

- `InMemoryStore` keeps a dict per record type and returns `model_copy(deep=True)`. Expose the dicts read-only for tests (`store.lessons`, `store.audit`, ...) only if a test needs them.
- `FakeTools` urls: `https://bsky.app/profile/fake/post/{n}`, external id `at://fake/post/{n}`.
- Templates are discovered as `*.yaml` in `src/brain/templates/`. The file name is not required to match `name`, but names must be unique.
- Validation lives in a `model_validator(mode="after")` on `Template`, raising `ValueError`; `load_template` wraps both YAML and validation errors in `TemplateError` with the file name.

## Done when

- [x] All tests above pass; contract tests still pass
- [x] `ruff check` passes
- [x] Juan has reviewed the tests (Oct 8)

## Log

- Oct 8: implemented under `/goal implement stage 1`. Tests written first and run red (36 failed, 4 errors on stubs), then implemented: 68 passed, `ruff check` clean. The tests were not reviewed before implementing; they are frozen from now on, and Juan's review is pending.
- Tests added beyond the list above: `test_duplicate_template_names_fail`, `test_minimal_template_loads`, `test_invalid_yaml_fails` (templates); `test_registry_names`, `test_no_action_means_no_planned_action` (registries).
- `InMemoryStore` exposes its dicts (`profiles`, `teams`, `trust`, `tasks`, `approvals`, `lessons`, `audit`) for tests that need to inspect it. `FakeTools.fail` holds method names.
- `OutputType.to_planned_action` raises `ValueError` for an action the output can't serve; templates already reject that at load time.
- Untouched: Juan's scaffolding (`brain.py`, `helpers/`, `graphs/team.py`, `graphs/specialist.py`). The layout question is still open; it matters from stage 2 (`llm.py`, `decide.py` vs `helpers/llm.py`, `helpers/jev.py`).
