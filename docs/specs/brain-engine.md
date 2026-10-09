# Spec: brain team engine (v0.2 draft)

Owner: Juan. Brain-internal: nothing here is contract except where it points to [CONTRACT.md](../CONTRACT.md).
Read [CONTEXT.md](../CONTEXT.md) first. The build plan, one file per stage, lives in [docs/brain/](../brain/README.md).

## 0. Changelog

**v0.3 (Oct 9), proposed by Mark with contract v0.6, agreed by Juan.** The designs live in the stage files until each stage lands; fold them into the sections below then.

| # | Change | Where |
| --- | --- | --- |
| 1 | Attachments become text before TRIAGE (transcripts, descriptions, document text); founder photos can go into posts | [stage 10](../brain/stage-10-attachments.md) |
| 2 | Image generation (OpenRouter Images API), `generate_image` tool, `image` output, Design template | [stage 11](../brain/stage-11-design-team.md) |
| 3 | `with_teams`: a task type's extra steps run by another hired team; images merged into the draft | [stage 12](../brain/stage-12-campaign-visuals.md) |
| 4 | `avatar` on every persona, pictures in `assets/avatars/` | [stage 13](../brain/stage-13-avatars.md) |
| 5 | Schedules: triage questions, `schedule` and `change_schedule` nodes, `run_schedule` never interrupts | [stage 14](../brain/stage-14-schedules.md) |

**v0.2 (Oct 8)**

| # | Change | Why |
| --- | --- | --- |
| 1 | New §2 Layers: facade, graph factories, one generic specialist subgraph, streaming and interrupts | How one engine serves every team |
| 2 | Team graph (§3.1): entry has two checks (feedback? work?); ROUTE runs before LEAD_PLAN | A message can be feedback and a task at once; briefs depend on the task type |
| 3 | ROUTE picks task types with one yes/no question per task type | A request can need several task types; specialists follow from the task type's `steps` |
| 4 | Plan structure is checked by code; unclear requests become an `Ask`, never a re-plan | Jev only says yes/no; code errors explain themselves |
| 5 | CHECK is code → `decide()` → critic LLM on revise (§5) | Jev can't explain a "revise"; the critic writes the feedback |
| 6 | No plan approval. GATE never interrupts; approvals are of results, outside the graph | Avoid double approvals; drafts are approved independently (CONTRACT §5) |
| 7 | Task types without an action at an acting level: the lead posts the result as a `Say` | `competitor_check` had no defined behaviour |
| 8 | §9 `decide()` pattern: code checks structure, Jev decides, LLM explains only on failure | One rule for every judgement call |
| 9 | §9 `decide()` takes several questions at once; Jev details (Decisions API, `noul` / `choice`); models | Matches how Jev on OpenRouter actually works (tested Oct 8) |
| 10 | Interrupt rule (§2); LEAD_PLAN returns its own clarifying question; ONBOARD asks one question per pass; revise reloads from the Store | Nodes re-run from the top on resume |
| 11 | Earlier steps of a task produce `notes`; only the last step produces the task type's `output` (§4) | The researcher in a newsletter doesn't write the email |
| 12 | Every `decide()` question puts its safe option last (§9) | One rule for low-confidence and failed decisions |
| 13 | TRIAGE replaces HAS_FEEDBACK + WANTS_WORK + ROUTE: one Jev call; no routed task type means no work (§3.1, §9) | "Does it ask for work?" was unreliable on live Jev; per-task-type questions are sharp |
| 14 | Routing has its own threshold, `ROUTE_THRESHOLD` = 0.6 (§9) | A missed request is worse than an extra draft that waits for approval |
| 15 | The check also sees the business profile (§5) | Tone and invented prices can't be judged without it |

## 1. Principles

- **One engine for every team.** Teams differ only by their YAML template. No per-team graph code.
- **Fixed steps per task type.** The YAML says which specialists a task type runs through, in order. The brain decides *which task types* a request needs and the lead writes each brief; nobody improvises the routing.
- **Plain code for rules, LLMs for work, `decide()` for judgement calls.**
- **Sequential now, parallel-ready.** Tasks of one request run one after another. All per-task state is keyed by `task_id`, so switching to LangGraph `Send` later changes the dispatcher only.
- **Approvals live outside the graph.** The graph pauses only for `Ask`.

## 2. Layers

```
Brain (facade, plain Python)       picks the thread, starts or resumes a graph, turns the stream into Events,
 │                                 catches every exception and yields Error
 ├── company graph                 business onboarding, then the chief of staff      thread "{business_id}:company"
 ├── team graph                    the orchestrator (the lead)                        thread "{business_id}:{team_id}"
 │     └── specialist subgraph     one generic subgraph, invoked once per step
 └── flows/                        approvals, promotion, undo: plain code, no graph
```

- **The facade routes.** `team_id is None` → company graph; otherwise → team graph. There is no top-level graph above them.
- **Graphs are built by factories**: `build_team_graph(deps, checkpointer)`, compiled once at startup. Nodes close over `Deps` (Store, Tools, LLM, decide, templates, settings), so tests pass fakes and production passes the app's Store and Tools. No globals.
- **The graph's shape is the same for every team.** Conditional edges are plain-code routers that read state (`task.steps`, `task.current_step`, check results). The template is looked up from `state["template"]` at runtime.
- **The specialist subgraph is called, not wired in.** The team graph's `specialist` node resolves persona, tools and output schema from the template and calls `run_specialist(specialist_graph, state)`, which streams the subgraph and re-emits its events on the team graph's stream (a subgraph's custom events don't reach the parent on their own). It has its own state schema (§4) and its tools are bound at runtime from tool names.
- **Events:** nodes emit contract `Event`s with LangGraph's stream writer (`get_stream_writer()`). The facade streams with `stream_mode=["custom", "updates"]`, yields custom chunks as-is, and turns an `__interrupt__` into the `Ask` it carries.
- **Ask and resume:** a node calls `interrupt(ask)`. On the next `handle_message` for the same thread, the facade sees the pending interrupt (`aget_state(...).interrupts`) and resumes with `Command(resume=text)`. Otherwise it starts a new request on the thread.
- **Interrupt rule:** on resume, LangGraph re-runs the interrupted node from the top. So a node that calls `interrupt()` does nothing before it that has side effects or costs money (no Store writes, no events, no LLM calls), and calls `interrupt()` at most once. The work that produces the question happens in the node before it.

| Entry (`Brain` method) | Runs | Thread id |
| --- | --- | --- |
| `start_onboarding`, `handle_message` with `team_id=None` | Company graph | `{business_id}:company` |
| `hire_team`, `handle_message` with a `team_id` | Team graph | `{business_id}:{team_id}` |
| `resolve_approval`, `respond_promotion`, `undo_action` | Plain-code flows (§6); reject with a reason re-enters the team graph to revise | none / the team's thread |

## 3. Graphs

### 3.1 Team graph

```
entry ─▶ revise input? ──yes──────────────────────────────────────────────▶ DISPATCH (rerun last step of that task)
   │ no
   ▼
 team onboarded? ──no──▶ ONBOARD (one YAML question per pass via Ask, saves the answer as a Lesson) ──▶ loop ──▶ END
   │ yes
   ▼
 TRIAGE           ONE decide() call on the team context + message:
   │                has_feedback  "does this message contain feedback or a preference?"
   │                needs_<type>  one yes/no per task type: "is the founder asking for <description>?"
   │              feedback yes → REFLECT → save_lesson → LessonLearned (lead confirms)
   │              no task type routed → LEAD_REPLY (one Say; never writes a draft) → END
   ▼             (there is no separate "does it ask for work?" question: no routed type = no work)
 LEAD_PLAN        LLM structured output: 1 TaskPlan {task_type, title, brief} per routed task type (max 3),
   │              plus the question it would ask if something is missing
   │              code validates the structure (known task type, non-empty brief, ≤ max_tasks_per_request)
   │              decide("is the request clear enough?") below threshold → CLARIFY (interrupt with the plan's
   │              question; the answer is appended to the request) → ROUTE (needs_<type> again, no
   │              feedback check) → LEAD_PLAN. At most 1 clarification.
   │              saves each Task (PLANNED)
   ▼
 DISPATCH ◀───────────────┐   plain code: next open task, next step of that task (task → IN_PROGRESS)
   │                      │
   ▼                      │
 SPECIALIST               │   invokes the specialist subgraph (§4), writes state.tasks[task_id].outputs
   │ ─────────────────────┘   current_step + 1; control returns to DISPATCH after every step
   │ all steps of the task done
   ▼
 CHECK                    §5: hard validators → decide → critic on revise
   │ revise and revisions < 2 → DISPATCH (rerun the last step with the critic's feedback)
   ▼
 GATE                     plain code: read Trust fresh (§6.4)
   │                      draft_only / act_after_approval → save Approval, task → WAITING_APPROVAL, NeedsApproval
   │                      act_and_report / autonomous     → EXECUTE → log_action → ActionDone, task → DONE
   │                      (no action at an acting level   → lead posts the result as a Say, task → DONE)
   ▼
 (more open tasks? → DISPATCH)
   ▼
 LEAD_REPORT              one Say summarising: what was done, what waits for approval. Doesn't repeat drafts.
```

- **No plan approval.** The lead proposes; the founder judges the result. The graph interrupts only when the request is unclear (or during team onboarding).
- **Answers to an `Ask` never go through TRIAGE**: they resume the paused node.
- **Revise input** carries the `approval_id`. The entry node reloads the task and the rejected draft from the Store, so revising doesn't depend on what the checkpoint still holds.
- **Limits:** `steps_used` and `tokens_used` are checked at DISPATCH against the template's `limits`. Over budget → the task is `FAILED`, the stream yields a recoverable `Error` for it, and the graph moves on to the next task.

### 3.2 Company graph

Business onboarding, until `Store.get_profile(...)` returns a profile:

1. The first question is fixed text (no LLM): business name, what they sell, and "paste your website".
2. Loop:
   - **Ask:** the LLM asks one or two questions in the chief-of-staff persona, targeting the fields still missing (a fixed fallback question if it fails). Question and wait are separate nodes (interrupt rule).
   - **Extract:** any links in the answer are fetched (`Tools.fetch_page`, max 2). LLM structured output pulls *every* field the answer or the website supports into a partial profile (kept in state); links are kept.
   - **Check:** `decide("answer_clear")`; an unclear answer that taught nothing → the next question is a follow-up.
   - **Completeness** is plain code: name, what they sell and customers filled, and tone either given, asked once, or 8 turns used.
3. Save the profile and save extra things learned as business-wide `fact` lessons. Yield `OnboardingComplete(scope="business")`.

Measured on Oct 8: pasting a website link fills the whole profile in one answer (name, offer, customers, prices, tone, named clients) once the extraction prompt asks for every field the website supports.

After onboarding, a message in the General topic gets one chief-of-staff `Say` (short answer, suggests hiring a team). Nothing more for the hackathon.

## 4. Specialist subgraph (generic)

Configured by the YAML entry plus the task's brief:

- **Output:** the last step of a task produces the task type's `output`. Earlier steps produce `notes` (`{notes, sources}`), which later steps see in their context.
- **State:** `persona`, `tool_names`, `output` (registry name, or `notes`), `brief`, `context` (business profile, relevant lessons, approved examples for this task type, earlier steps' outputs for this task, feedback from failed checks or rejects), `messages`, `result`.
- **Loop:** agent ⇄ tools, an LLM with tools, up to `max_steps_per_specialist`. When the LLM stops calling tools (or the limit is hit), a final structured-output call turns the conversation into the output schema.
- **Returns** `result` and the tokens it used; the team graph's `specialist` node writes it to `state.tasks[task_id].outputs[specialist_id]`.
- **Yields** `Progress` events ("Sam is researching competitors…").
- **Never** calls another specialist and never executes a real action. Actions happen only at GATE / EXECUTE.

## 5. Check

Independent: it never reuses the lead's or the specialist's prompt.

1. **Hard validators** (code, per output type): Bluesky ≤ 300 characters, email has a subject, no empty fields, no placeholder text like `[Name]`. A failure is a revise with the validator's message as feedback, and no `decide()` call.
2. **Judgement:** `decide("does the draft pass?", ["pass", "revise"], ...)` against the business profile, the task type's `check` rules and the team's active lessons. The confidence becomes `check_confidence`. Below the threshold counts as revise. Without the profile, "matches the business tone" and "no invented prices" can't be judged: measured on Oct 8, adding it raised good drafts from 0.68–0.71 to 0.78–0.87.
3. **Critic (only on revise):** one LLM structured-output call lists the issues ("uses hashtags; breaks 'No hashtags'"). The issues go back to the last step as feedback, and `revisions + 1`.
4. **After 2 revisions** the draft goes to approval anyway, with its low confidence, rather than looping.

## 6. Plain-code flows (outside the graph)

### 6.1 `resolve_approval`

Load the approval and check it's pending (otherwise a recoverable `Error`). Then branch on the decision table in CONTRACT §7:

- **approve:** execute the action (if any), log it, update trust, run the promotion rule (§6.4), have the lead confirm with a `Say`. Task → `DONE`.
- **edit:** execute the founder's version, update trust, REFLECT on (original, edited). Task → `DONE`. Since contract v0.4 the founder's version is `edited_action` when the app sends one (an email with a new subject or recipient); it must have the draft's `tool`, otherwise a recoverable `Error`. Without it, the planned action with `edited_text`. REFLECT sees the whole edited email.
- **reject:** update trust. With a reason: REFLECT on it, and if revisions are left, re-enter the team graph for that `task_id` with `{"revise": task_id, "feedback": reason}`; the entry router sends it straight to DISPATCH, which reruns the last step, then CHECK and GATE. Without a reason: task → `REJECTED`.

### 6.2 `respond_promotion`

Accept: level + 1, streak 0. Decline: streak 0. Then a `Say` from the lead.

### 6.3 `undo_action`

As in CONTRACT §9.

### 6.4 Autonomy rules (pure functions)

- **Gate:** `draft_only`, `act_after_approval` → approval; `act_and_report` → act and report; `autonomous` → act, `autonomous=True`.
- **Streak:** approve without edits → + 1; edit, reject, undo → 0; actions without approval don't change it.
- **Promotion (contract v0.3):** streak ≥ `trust.promote_after` (default 5, set by the founder on the dashboard), the last `promote_after` resolved approvals (`recent_approvals(limit=promote_after)`) all approved at `check_confidence ≥ 0.8`, level below the template's `max_level` → `PromotionOffer` for the next level. The evidence names the number ("your last 3 social post drafts").

## 7. Learning loop

| Signal | When | Becomes |
| --- | --- | --- |
| Team onboarding answer | Team graph ONBOARD | `fact` lesson, `key` = question key, team scope |
| Business onboarding extras | Company graph, end of onboarding | `fact` lessons, business-wide |
| Edit | `resolve_approval` | REFLECT on the diff → `preference` lesson(s) |
| Reject with reason | `resolve_approval` | REFLECT on the reason → `preference` lesson(s) |
| Chat feedback | Team graph TRIAGE (`has_feedback`) | REFLECT on the message → lesson(s) |
| Approved draft | `Store.recent_approvals` at prompt time | Few-shot examples (not stored as lessons) |

**REFLECT** is one function, `reflect(feedback, existing_lessons) -> list[Lesson]`, in `brain/learning.py`: one LLM structured-output call (`MODEL_REFLECT`). It is not a graph or a persona. It returns 0–2 lessons, each with:

- **text:** short and imperative ("No emojis in posts")
- **kind:** `fact` or `preference`
- **scope:** team or business-wide
- **task_type:** optional
- **replaces:** optional id of an existing lesson it repeats or contradicts

**Duplicates:** a lesson with `replaces` deactivates the old one (`active=False`) before the new one is saved, rather than piling up.

Each saved lesson yields a `LessonLearned` event, and the lead confirms it in chat.

**Prompt budget:** at most ~20 active lessons and 3 approved examples per call, newest first. If a team goes over, a consolidation pass merges lessons (later, only if needed).

## 8. Team template (YAML)

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

- **Registries** (Python, brain-side): `tools` (names → callables over `Tools` / `Store`), `outputs` (names → Pydantic schemas + hard validators + `preview()` + `to_planned_action()`), `actions` (`post_social`, `send_email`). Templates refer to them by name, and a template is validated at load time: unknown names fail fast.
- `read_memory` is a brain tool that reads the profile and lessons through `Store`.
- `list_templates()` builds `TemplateInfo` from these files.
- Persona names are placeholders.

## 9. `decide()`

```python
class Question(BaseModel):
    instructions: str
    options: dict[str, str]  # option → what it means; two options = yes/no

class Decision(BaseModel):
    choice: str  # always one of the question's options
    confidence: float  # 0..1

async def decide(questions: dict[str, Question], state: str) -> dict[str, Decision]:
    """
    All questions in one Jev call; falls back to LLM structured output if Jev fails or returns an invalid choice.
    """
```

**Pattern for every judgement call: code checks the structure, Jev decides, an LLM explains only when the answer is "no".**

| Decision | Options | Below threshold / "no" |
| --- | --- | --- |
| Does this message contain feedback? | yes / no | no (don't invent lessons) |
| Is the founder asking for `<description>`? (one question per task type, same call) | yes / no | not routed (`ROUTE_THRESHOLD`); none routed → lead replies |
| Is the request clear enough? | clear / unclear | lead asks (`Ask`) |
| Does the draft pass? | pass / revise | critic writes feedback, revise |
| Is an onboarding answer clear? | clear / unclear | follow-up question |

**Safe option last.** Every question lists its safe option last (`no`, `unclear`, `revise`). If Jev and the fallback both fail, `decide()` returns the safe option with confidence 0. Callers accept a choice only when it is the risky option *and* the confidence is above the threshold.

Threshold starts at 0.7 (`DECIDE_THRESHOLD`, tune Thursday). Routing uses its own, lower bar, 0.6 (`ROUTE_THRESHOLD`): a wrong "yes" costs a draft that still waits for approval, a wrong "no" ignores the founder. Promotions use a separate 0.8 bar on `check_confidence`.

**Measured on Oct 8 (live Jev):** a bare "does this message ask for work?" question was unreliable (0.54–0.61 on plain requests). Per-task-type questions scored 0.94–0.99 on explicit requests once the state named the team and what it makes (`prompts/lead.py` `triage_state`) and the template descriptions said what each deliverable is for. The vague demo line "We launch Friday, get the word out" routes post 0.76 and newsletter 0.69.

### Jev on OpenRouter

- **Model:** `typesafe/jev-1.13` (`MODEL_JEV`). Not a chat model: no text, no explanations.
- **Endpoint:** `POST https://openrouter.ai/api/alpha/decisions` with the OpenRouter key. Not chat completions, so it's a plain HTTP call, not the OpenAI SDK.
- **Request:** `{"model", "state", "questions": {key: {"type", "instructions", "criteria"}}}`. `state` is the context as text (max 32k prompt tokens; the brain truncates).
- **Question types we use:** `noul` (yes/no, `criteria: {"true": ..., "false": ...}`) for two-option questions; `choice` (`criteria: {option: meaning}`) for more.
- **Response:** `answers[key]`. `choice` gives `choice`, `probabilities` and `confidence`. `noul` gives only `noul`, the probability of yes: the brain maps it to `choice = yes if noul ≥ 0.5`, `confidence = max(noul, 1 - noul)`.
- **Batching:** questions in one request are answered in parallel and can't see each other. TRIAGE is one call: `has_feedback` plus a `noul` per task type.
- **Usage:** `usage.input_tokens` counts toward `tokens_used`.
- **Fallback:** any HTTP error, timeout or missing / invalid answer → the same questions as one LLM structured-output call (`MODEL_DECIDE_FALLBACK`), which returns a choice and a self-reported confidence per question.

### Models

All LLM roles start on `deepseek/deepseek-v4-flash-0731` (cheap, supports tools and structured outputs): `MODEL_LEAD`, `MODEL_SPECIALIST`, `MODEL_REFLECT`, `MODEL_DECIDE_FALLBACK`. Each is overridable through its env var, so roles can use different models.

**Reasoning** (`LLM_REASONING`, default `true` = the model's own behaviour). Measured on Oct 9: DeepSeek spent 63–1,257 reasoning tokens on a two-sentence post (5–27 s a call); with `false` the brain sends `reasoning: {enabled: false}` and calls take 3–4 s. One live post request took 149 s with reasoning and 56 s without. Turn it off for testing and the 3-minute demo; leave it on where judgement matters more than speed. Every call has a timeout (`LLM_TIMEOUT`, 60 s, one retry); before that, one call hung for minutes on the SDK's 10-minute default.

**Output guidance:** each output type carries its hard rules in words (`OutputType.guidance`, e.g. "at most 300 characters, aim for about 250"), and the specialist prompt includes them. Without it, the writer only learned the Bluesky limit from failed checks.

## 10. State sketch

```python
class TaskState(BaseModel):
    task: Task  # contract model
    outputs: dict[str, dict] = {}  # specialist_id → structured output
    feedback: list[str] = []  # from failed checks / rejects
    check_confidence: float | None = None

class TeamState(TypedDict):
    business_id: str
    team_id: str
    template: str
    request: str
    message_id: str | None
    revise: str | None  # task_id to revise (reject with reason)
    tasks: dict[str, TaskState]  # keyed by task_id: parallel-ready
    order: list[str]  # dispatch order (sequential for now)
    steps_used: int
    tokens_used: int
```

## 11. Module layout

```
src/brain/
  common.py            new_id(), utcnow()
  deps.py              Settings (env vars), Deps
  helpers/llm.py       LLM protocol + OpenRouter chat client
  helpers/jev.py       Jev protocol + OpenRouter Decisions API client
  helpers/decide.py    Question, Decision, decide()
  context.py           builds the prompt context: profile, lessons, approved examples
  tools.py             brain tools: read_memory, web_search, fetch_page
  check.py             hard validators, judgement, critic
  learning.py          reflect(), learn()
  graphs/team.py       build_team_graph
  graphs/specialist.py build_specialist_graph
  graphs/company.py    build_company_graph
  flows/               autonomy.py, actions.py, hire.py, approvals.py, promotion.py, undo.py
  templates/           marketing.yaml, models.py, loader.py, registries.py
  prompts/             one file per role (lead, specialist, check, critic, reflect, chief_of_staff)
  brain.py             the facade: TenureBrain (the Brain implementation), create_brain()
  checkpoint.py        in-memory or Postgres checkpointer
  fakes.py             InMemoryStore, FakeTools, FakeLLM, FakeJev
  cli.py               terminal chat
```

## 12. Open (brain-side)

- Exact prompts for every role (`src/brain/prompts/`).
- A "make it shorter" message while a draft is pending is really a reject with a reason. Out of scope for the demo.
