# Stage 6: Team graph and facade (demo slice)

**Status:** not started
**Depends on:** stages 3, 4, 5
**Spec:** [brain-engine.md](../specs/brain-engine.md) §2 (layers, interrupt rule), §3.1 (team graph), §9 (`decide()`); [CONTRACT.md](../CONTRACT.md) §4 (Brain), §5 (stream lifecycle), §6 (events)

## Goal

The orchestrator for every team, and the `Brain` implementation the app calls. At the end of this stage Mark can plug in the real brain: hire marketing, answer its onboarding questions, ask for a post, and get a `NeedsApproval` that posts on approve.

Learning from chat and from edits / rejects is wired in stage 7; here the graph has an optional `learn` hook that is `None`.

## Files

| File | Contains |
| --- | --- |
| `src/brain/graphs/team.py` | `TaskState`, `TeamState`, `build_team_graph()` |
| `src/brain/prompts/lead.py` | `LeadPlan`, `TaskPlan`, the lead's plan and reply messages, report text |
| `src/brain/brain.py` | the facade: `TenureBrain`, `create_brain()` (replaces Juan's placeholder `Brain` class) |
| `tests/brain/conftest.py` | adds `brain` fixture (a `TenureBrain` on fakes, in-memory checkpointer) and `collect(aiter)` |

## Interfaces

```python
# graphs/team.py
class TaskState(BaseModel):
    task: Task
    phase: Literal["work", "check", "gate", "done"] = "work"
    outputs: dict[str, dict] = {}         # specialist_id → result
    feedback: list[str] = []
    check_confidence: float | None = None

class TeamState(TypedDict, total=False):
    business_id: str
    team_id: str
    template: str
    request: str
    message_id: str | None
    revise: str | None                    # approval_id to revise (reject with a reason)
    feedback: str | None                  # the founder's reason, with `revise`
    routed: list[str]                     # task types picked by ROUTE
    plan_question: str | None
    clarified: int
    onboarding_answers: dict[str, str]
    tasks: dict[str, TaskState]           # keyed by task_id
    order: list[str]                      # task_ids of this request, dispatch order
    tokens_used: int

LearnHook = Callable[[TeamState, str], AsyncIterator[Event]]   # (state, message) → LessonLearned events; stage 7
def build_team_graph(deps: Deps, checkpointer, learn: LearnHook | None = None) -> CompiledStateGraph: ...

# prompts/lead.py
class TaskPlan(BaseModel):  task_type: str; title: str; brief: str
class LeadPlan(BaseModel):  tasks: list[TaskPlan]; question: str | None = None

# brain.py (the facade)
class TenureBrain:                         # satisfies contract.Brain
    def __init__(self, deps: Deps, checkpointer=None): ...      # None → InMemorySaver
    def list_templates(self) -> list[TemplateInfo]: ...
    def start_onboarding(self, business_id: str) -> AsyncIterator[Event]: ...   # stage 8; until then one Error
    def handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]: ...
    def hire_team(self, business_id: str, template: str) -> AsyncIterator[Event]: ...
    def resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]: ...
    def respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]: ...
    def undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]: ...

def create_brain(
    store: Store, tools: Tools, settings: Settings | None = None,
    checkpointer=None, llm: LLM | None = None, jev: Jev | None = None,
) -> TenureBrain: ...
    # what the app calls: settings from env, OpenRouterLLM / OpenRouterJev by default, templates loaded
```

### Graph

Node by node (spec §3.1):

| Node | Does | Next |
| --- | --- | --- |
| `entry` | loads the team. `revise` set → loads the approval and task from the Store, builds a `TaskState` with the rejected draft and reason as feedback, `revisions + 1`, `current_step = last`, `order = [task_id]` | `revise` → `dispatch`; not onboarded → `onboard`; else `triage` |
| `onboard` | next unanswered template question → `interrupt(Ask(...))`; after resume saves a team `fact` lesson (`key`, `source="team_onboarding"`, text `"{question} {answer}"`) | more questions → `onboard`; else `onboard_done` |
| `onboard_done` | `team.onboarded = True`, `OnboardingComplete(scope="team")`, lead `Say` ("Thanks! What should we work on first?") | END |
| `triage` | one `decide()`: `has_feedback`, `wants_work`. Feedback accepted and `learn` set → yields its events | work accepted → `route`; else `reply` |
| `reply` | lead LLM `complete()` → one `Say` (acknowledge, short answer, or say what the team can do) | END |
| `route` | one `decide()` with `needs_{task_type}` per task type; keeps the accepted ones, max `max_tasks_per_request` | none → `reply`; else `plan` |
| `plan` | lead `structured(LeadPlan)` for the routed task types; code fixes the structure (drops unrouted task types, empty brief → the request, caps the count). If `clarified == 0`: `decide()` `is_clear` | not clear and a question → `clarify`; else saves each `Task` (`PLANNED`) → `dispatch` |
| `clarify` | `interrupt(Ask(plan_question))`; appends `"\n\nFounder: {answer}"` to the request; `clarified + 1` | `route` |
| `dispatch` | picks the first task in `order` whose phase isn't `done`. Over `token_budget` → task `FAILED`, recoverable `Error`, phase `done` | `work` with steps left → `specialist`; `check` → `check`; `gate` → `gate`; none left → `report` |
| `specialist` | invokes the specialist subgraph for `steps[current_step]` (output = task type's output on the last step, else `notes`; context from `build_context` with prior outputs and feedback); task `IN_PROGRESS`; `current_step + 1`; tokens added. Exception → task `FAILED`, recoverable `Error`, phase `done` | last step done → phase `check`; → `dispatch` |
| `check` | lead `Progress` ("Maya is reviewing the draft"); `run_check` on the last step's output with the team's lessons. Passed, or `revisions == 2` → phase `gate` with `check_confidence`. Else `revisions + 1`, feedback = the check's feedback, `current_step = last`, phase `work` | `dispatch` |
| `gate` | reads `Trust` fresh. `approval` → saves `Approval` (`planned_action` is `None` for `draft_only`), task `WAITING_APPROVAL`, yields `NeedsApproval`. `act` / `act_autonomous` with an action → `execute_action`, task `DONE`. With no action → lead `Say` with the preview, task `DONE`. Phase `done` | `dispatch` |
| `report` | one lead `Say` built by code: what was done and what waits for approval, by task title. Nothing to report (all failed) → no `Say` | END |

Every task change is saved with `store.save_task` and a fresh `updated_at`. Task-related events carry `task_id`.

### Decision keys

| Key | Question (options, safe last) | Used in |
| --- | --- | --- |
| `has_feedback` | Does the message give feedback or a preference about how the team works? (`yes`, `no`) | `triage` |
| `wants_work` | Does the message ask the team to make or do something? (`yes`, `no`) | `triage` |
| `needs_{task_type}` | Does the request need a {description}? (`yes`, `no`) | `route` |
| `is_clear` | Is there enough information to do this well without asking? (`clear`, `unclear`) | `plan` |
| `passes_check` | stage 4 | `check` |
| `answer_clear` | stage 8 | company graph |

### Facade

- **Threads:** `{business_id}:{team_id}` and `{business_id}:company` (CONTRACT §3).
- **`handle_message`** with a `team_id`: unknown team or another business → recoverable `Error`. Pending interrupt → `Command(resume=msg.text)`. Else a new request: `{"business_id", "team_id", "template", "request", "message_id", "revise": None, "routed": [], "clarified": 0, "tasks": {}, "order": [], "tokens_used": 0}`. With `team_id=None` → the company graph (stage 8; until then one recoverable `Error`).
- **`hire_team`**: runs `flows.hire_team`; after `TeamHired` runs the team graph on the new thread with an empty request, which goes straight to `onboard` → the first `Ask`.
- **`resolve_approval`**: `flows.resolve_approval` with `revise` = run the team graph on that team's thread with `{"revise": approval_id, "feedback": reason, ...}`; `learn` is `None` until stage 7.
- **Streaming:** `astream(payload, config, stream_mode=["custom", "updates"])`. A `custom` chunk is an `Event` → yield it. An `updates` chunk with `__interrupt__` → yield its `Ask` value.
- **Errors:** every public method wraps its work; any exception → one `Error(team_id=..., message="Something went wrong on my side. Try again in a moment.", recoverable=True)`. Nothing raises out of the iterator.

### How the app builds the brain

```python
from brain.brain import create_brain
brain = create_brain(store=supabase_store, tools=real_tools)   # keys from env; in-memory checkpoints until stage 9
```

## Tests

All through `TenureBrain` with `FakeLLM`, `FakeJev`, `InMemoryStore`, `FakeTools` and an in-memory checkpointer.

`tests/brain/test_team_hire.py`

- `test_hire_yields_team_hired_then_first_onboarding_ask`: `TeamHired` is the first event; the stream ends with an `Ask` on the new `team_id` from Maya.
- `test_onboarding_answers_become_team_facts`: answering 3 times → 3 lessons with `key` and `source="team_onboarding"`, `team.onboarded`, `OnboardingComplete(scope="team")`, then a `Say`.
- `test_team_without_onboarding_questions_is_onboarded_at_once`

`tests/brain/test_team_work.py`

- `test_post_request_ends_in_needs_approval`: `needs_social_post` yes → `NeedsApproval` with `PostSocial`, then a `Say` report. Task `WAITING_APPROVAL` and `Approval` pending in the Store; `check_confidence` from the check.
- `test_progress_events_carry_task_id`
- `test_two_task_types_give_two_approvals`: distinct `approval_id` and `task_id`; dispatch order follows the plan.
- `test_newsletter_runs_researcher_then_writer`: the writer's context contains the researcher's notes.
- `test_plan_drops_task_types_route_did_not_pick`
- `test_unclear_request_asks_then_resumes`: `is_clear` unclear → `Ask` with the plan's question; the reply resumes; work continues to `NeedsApproval`; no second clarification.
- `test_failed_check_reruns_writer_with_feedback`: check fails once → the writer's second prompt has the critic's issue → passes → `NeedsApproval`; `task.revisions == 1`.
- `test_two_failed_checks_go_to_approval_anyway`: low `check_confidence`, `revisions == 2`.
- `test_act_and_report_acts_without_approval`: trust set to `act_and_report` → `ActionDone`, no `NeedsApproval`, audit `autonomous=False`.
- `test_autonomous_marks_action_autonomous`
- `test_task_type_without_action_posts_result_as_say`: `competitor_check` → a `Say` with the report, task `DONE`.
- `test_draft_only_approval_has_no_planned_action`
- `test_message_without_work_gets_one_say`: no tasks saved.
- `test_no_routed_task_type_gets_one_say`
- `test_over_token_budget_fails_task_with_error`: stream ends cleanly; task `FAILED`.
- `test_specialist_exception_fails_task_not_stream`: the next task still runs.

`tests/brain/test_facade.py`

- `test_list_templates`
- `test_unknown_team_yields_error`
- `test_message_from_other_business_yields_error`
- `test_graph_exception_yields_error_never_raises`
- `test_reject_with_reason_revises_into_new_approval`: approve flow + `revise` hook → a new `NeedsApproval` with a new `approval_id`; `task.revisions` incremented.
- `test_threads_are_separate_per_team`: a pending `Ask` in one team doesn't affect a request in another.
- `test_create_brain_builds_from_env`: with fake store / tools and a settings object, no network.

## Implementation notes

- `TeamState.tasks` has no reducer: nodes return the whole dict with their task replaced. Parallel tasks later need a merge reducer here and `Send` in `dispatch`.
- The `specialist` node passes its `config` to `specialist_graph.ainvoke(..., config)` so the subgraph's `Progress` events reach the facade's stream.
- Respect the interrupt rule: `onboard` and `clarify` do nothing before `interrupt()`.
- A reject-with-reason that arrives while the team thread is paused on an `Ask` starts a new run on that thread and drops the pending question. Acceptable for the demo.

## Done when

- [ ] All tests above pass; earlier stages still pass
- [ ] `ruff check` passes
- [ ] Manual check: one real run against OpenRouter from a scratch script (hire → onboarding → "post about our Friday launch" → approve)

## Log

_Empty._
