# Spec: brain team engine (v0.1 draft)

Owner: Juan. Brain-internal: nothing here is contract except where it points to [CONTRACT.md](../CONTRACT.md).
Read [CONTEXT.md](../CONTEXT.md) first.

## 1. Principles

- **One engine for every team.** Teams differ only by their YAML template. No per-team graph code.
- **Fixed steps per task type.** The YAML says which specialists a task type runs through, in order. The lead decides *which task types* a request needs and writes each brief; it doesn't improvise the routing.
- **Plain code for rules, LLMs for work, `decide()` for judgement calls.**
- **Sequential now, parallel-ready.** Tasks of one request run one after another. All per-task state is keyed by `task_id`, so switching to LangGraph `Send` later changes the dispatcher only.
- **Approvals live outside the graph.** The graph pauses only for `Ask`.

## 2. Graphs

There are three entry points, and each runs one graph or one plain-code flow.

| Entry (`Brain` method) | Runs | Thread id |
| --- | --- | --- |
| `start_onboarding`, `handle_message` with `team_id=None` | Business onboarding graph, later the chief of staff | `{business_id}:company` |
| `hire_team`, `handle_message` with a `team_id` | Team graph | `{business_id}:{team_id}` |
| `resolve_approval`, `respond_promotion`, `undo_action` | Plain-code flows (§5); may re-enter the team graph to revise | none / the team's thread |

### 2.1 Team graph

```
entry ──▶ team onboarding? ──yes──▶ ONBOARD (asks YAML questions via Ask, saves facts as Lessons) ──▶ END
              │ no
              ▼
          LEARN_FROM_CHAT   decide("does this message contain feedback or a preference?")
              │             yes → REFLECT → save_lesson → LessonLearned
              ▼
          LEAD_PLAN         LLM: request → 1–3 TaskPlans {task_type, title, brief}
              │             decide("is the request clear enough?") < threshold → ASK (interrupt)
              ▼
          DISPATCH ◀──────────────┐   plain code: next pending task, next step of that task
              │                   │
              ▼                   │
          SPECIALIST              │   generic subgraph (§3), writes its output to state[task_id]
              │ ──────────────────┘   control returns to DISPATCH after every step
              │ all steps of a task done
              ▼
          CHECK                   hard validators (code) + rubric and lessons (decide)
              │ fail and revisions < 2 → DISPATCH (rerun last step with feedback)
              ▼
          GATE                    plain code: read Trust
              │  draft_only / act_after_approval → save Approval, yield NeedsApproval
              │  act_and_report / autonomous     → EXECUTE → log_action → ActionDone
              ▼
          (more tasks? → DISPATCH)
              ▼
          LEAD_REPORT             one Say: what was done, what waits for approval
```

### 2.2 Business onboarding graph

1. Optionally fetch the website (`Tools.fetch_page`) and extract prefill answers.
2. Loop:
   - **Ask:** the LLM asks one or two questions in the chief-of-staff persona, targeting the fields still missing.
   - **Extract:** LLM structured output pulls fields from the reply into a partial `BusinessProfile` (kept in state).
   - **Check:** `decide("is this answer clear enough?")` for ambiguous answers → follow-up question.
   - **Completeness** is plain code: all required fields filled.
3. Save the profile and save extra things learned as business-wide `fact` lessons. Yield `OnboardingComplete(scope="business")`.

## 3. Specialist subgraph (generic)

Configured by the YAML entry plus the task's brief:

- **Inputs:** persona, tools (resolved from the tool registry), the output schema (from the output registry), the brief, the business profile, relevant lessons, approved examples for this task type, and earlier steps' outputs for this task.
- **Loop:** an LLM with tools, up to `max_steps_per_specialist`. It ends with structured output in the task type's output schema.
- **Writes** only to `state.tasks[task_id].outputs[specialist_id]`.
- **Yields** `Progress` events ("Sam is researching competitors…").
- **Never** calls another specialist and never executes a real action. Actions happen only at GATE / EXECUTE.

## 4. Check

- **Hard validators** (code, per output type): Bluesky ≤ 300 characters, email has a subject, no empty fields, no placeholder text like `[Name]`.
- **Judgement:** `decide("does the draft pass?", ["pass", "revise"], ...)` against the task type's `check` rules plus the team's active lessons. The confidence becomes `check_confidence`.
- **On "revise":** the reason is passed back to the last step as feedback, and `revisions + 1`. After 2 revisions the draft goes to approval anyway, with its low confidence, rather than looping.

## 5. Plain-code flows (outside the graph)

**`resolve_approval`.** Load the approval and check it's pending. Then branch on the decision table in CONTRACT §6:

- **approve:** execute the action, log it, update trust, run the promotion rule (CONTRACT §7), have the lead confirm with a `Say`.
- **edit:** execute with the edited text, update trust, run REFLECT on (original, edited).
- **reject:** update trust and REFLECT on the reason. If there's a reason and revisions are left, re-enter the team graph for that `task_id` (rerun its last step with the reason as feedback, then CHECK and GATE).

**`respond_promotion`.** Accept: level + 1, streak 0. Decline: streak 0. Then a `Say` from the lead.

**`undo_action`.** As in CONTRACT §8.

## 6. Learning loop

| Signal | When | Becomes |
| --- | --- | --- |
| Team onboarding answer | Team graph ONBOARD | `fact` lesson, `key` = question key, team scope |
| Business onboarding extras | Business onboarding end | `fact` lessons, business-wide |
| Edit | `resolve_approval` | REFLECT on the diff → `preference` lesson(s) |
| Reject with reason | `resolve_approval` | REFLECT on the reason → `preference` lesson(s) |
| Chat feedback | Team graph LEARN_FROM_CHAT | REFLECT on the message → lesson(s) |
| Approved draft | `Store.recent_approvals` at prompt time | Few-shot examples (not stored as lessons) |

**REFLECT** is one LLM structured-output call. It returns 0–2 lessons, each with:

- **text:** short and imperative ("No emojis in posts")
- **kind:** `fact` or `preference`
- **scope:** team or business-wide
- **task_type:** optional

**Duplicates:** before saving, compare with existing lessons. If the new lesson contradicts or repeats one, deactivate the old one rather than piling up.

Each saved lesson yields a `LessonLearned` event, and the lead confirms it in chat.

**Prompt budget:** at most ~20 active lessons and 3 approved examples per call, newest first. If a team goes over, a consolidation pass merges lessons (later, only if needed).

## 7. Team template (YAML)

```yaml
name: marketing
display_name: Marketing
description: "Posts, newsletters and competitor checks in your voice"
lead:
  persona: { name: Maya, role: Marketing lead }
  instructions: "Keep the founder's time free; propose, don't ask, when in doubt."
specialists:
  writer:
    persona: { name: Leo, role: Writer }
    tools: [read_memory]
  researcher:
    persona: { name: Sam, role: Researcher }
    tools: [web_search, fetch_page, read_memory]
task_types:
  social_post:
    description: "A short post for Bluesky"
    steps: [writer]
    output: social_post            # registry: {text}
    check: ["matches the business tone", "no invented facts or prices"]
    action: post_social
    start_level: act_after_approval
    max_level: autonomous
  newsletter:
    description: "An email newsletter"
    steps: [researcher, writer]
    output: email                  # registry: {to, subject, body}
    check: ["clear subject line", "one call to action"]
    action: send_email
    start_level: act_after_approval
    max_level: act_and_report
  competitor_check:
    description: "What competitors are doing lately"
    steps: [researcher]
    output: report                 # registry: {summary, sources}
    check: ["every claim has a source"]
    action: null
    start_level: act_and_report
onboarding:                        # 2–3 questions max; answers become team-scoped fact lessons
  channels: "Which channels do you post on?"
  upcoming: "Any launch or event coming up?"
  newsletter_to: "Which address or list should newsletters go to?"
limits:
  max_tasks_per_request: 3
  max_steps_per_specialist: 6
  token_budget: 60000
```

- **Registries** (Python, brain-side): `tools` (names → callables over `Tools` / `Store`), `outputs` (names → Pydantic schemas + hard validators + a `to_planned_action()` mapping), `actions` (`post_social`, `send_email`). Templates refer to them by name, and a template is validated at load time: unknown names fail fast.
- `read_memory` is a brain tool that reads the profile and lessons through `Store`.
- `list_templates()` builds `TemplateInfo` from these files.
- Persona names are placeholders.

## 8. `decide()`

```python
class Decision(BaseModel):
    choice: str  # always one of `options`
    confidence: float  # 0..1

async def decide(question: str, options: list[str], state: dict) -> Decision:
    """Jev via OpenRouter; falls back to an LLM structured output if Jev fails or returns an invalid choice."""
```

Used for:
- which task types a request needs
- is the request clear enough or should we ask
- does this message contain feedback
- does the draft pass the check
- is an onboarding answer clear

Threshold starts at 0.7 (tune Thursday). Below it, take the safe path: ask, revise or escalate. Promotions use a separate 0.8 bar on `check_confidence`.

## 9. State sketch

```python
class TaskState(BaseModel):
    task: Task  # contract model
    outputs: dict[str, dict] = {}  # specialist_id → structured output
    feedback: list[str] = []  # from failed checks / rejects
    check_confidence: float | None = None

class TeamState(TypedDict):
    business_id: str
    team_id: str
    request: str
    tasks: dict[str, TaskState]  # keyed by task_id: parallel-ready
    order: list[str]  # dispatch order (sequential for now)
    steps_used: int
    tokens_used: int
```

## 10. Open (brain-side)

- Model choices per role (lead, specialist, reflect) on OpenRouter.
- Whether `LEAD_PLAN` and the task-type routing `decide()` are one call or two.
- Exact prompts for lead, specialist, check, reflect (`src/brain/prompts/`).
