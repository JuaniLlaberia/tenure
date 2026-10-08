# Brain ↔ App Contract (v0.3 draft)

This is the agreement between the agent brain (`src/brain/`, owner: Juan) and the app (`src/app/`, owner: Mark).
If both sides respect it, each can be built and tested alone, and they plug together at merge points.

- **`src/contract/`** defines the exact shapes: Pydantic models and Protocols. The code stays lean, without explanatory comments.
- **This file** explains what every model, field and method means, and the rules around them.

Both change together: a change to one without the other is a bug, and `tests/contract/` should catch the shape side.

## 0. Changelog

**v0.3 (Oct 8), proposed by Mark, needs Juan's OK**

| # | Change | Why |
| --- | --- | --- |
| 1 | `Trust` gains `promote_after` (default 5, minimum 1); the promotion rule uses it instead of a fixed 5 (§8) | The founder sets how many clean approvals earn a promotion offer, from the dashboard |

**v0.2 (Oct 5), proposed by Juan, needs Mark's OK**

| # | Change | Why |
| --- | --- | --- |
| 1 | Shared code lives in `src/contract/`, split into `brain_side/`, `app_side/` and `models/` (§2) | One import path; clear who owns what |
| 2 | New §3 Conventions: ID ownership, UTC, upserts, thread ids, `get_*` returns `None` | Avoid merge-day surprises |
| 3 | New §5 Stream lifecycle: what ends a stream, per-thread serialization, debounce | Undefined in v0.1 |
| 4 | `Brain.list_templates()` + `TemplateInfo` | App needs the hireable teams for the dashboard and `/hire` |
| 5 | `Approval` gains `business_id`, `team_id`, `task_type`, `check_confidence`, `edited_text`, `reason`, timestamps | v0.1 could not resolve an approval (no way to reach team or task type) |
| 6 | `Store.get_approval` returns `None` if missing; new `Store.recent_approvals()` | Approved examples for learning; check-confidence history for promotions |
| 7 | `create_team` → `save_team` / `get_team`; brain creates teams | App can't know a template's `display_name` |
| 8 | `get_trust` returns `Trust \| None`; brain creates trust rows on hire | Store doesn't know templates' `start_level` |
| 9 | Fixed name: `set_trust_level` → `set_trust` | Typo between §5 and §7 of v0.1 |
| 10 | `Task`: `business_id`, `brief`, `steps`, `current_step`, `updated_at`; `specialist` removed; new `REJECTED` status | Fixed steps per task type |
| 11 | `PlannedAction` is a typed union (`PostSocial` / `SendEmail`) instead of `args: dict` | Both sides validate the same shape |
| 12 | `AuditEntry` gains `tool`, `approval_id`, `undo_until`, `undone_at`; new `Store.get_action()`; new `ActionUndone` event | v0.1 could not undo (no way to find `external_id`) |
| 13 | Learning: `Lesson` model, `save_lesson` / `list_lessons`, `LessonLearned` event; `add_note` / `recent_notes` removed | Teams learn per business; lessons replace free-form notes |
| 14 | `Tools.fetch_page()` + `PageContent` | "Paste your website" onboarding |
| 15 | `Say` and `Progress` gain optional `task_id` | Ready for parallel tasks interleaving in one stream |
| 16 | Team templates (YAML) moved out to `docs/specs/brain-engine.md`; only `TemplateInfo` is contract | Brain-internal |
| 17 | v0.1 open questions decided (§13) | |
| 18 | Enums are `StrEnum`; this file describes the models in tables instead of repeating the code | Code is the exact shape, this file the meaning |

## 1. Ownership and rules

| Area | Owner | Rule |
| --- | --- | --- |
| `src/brain/` | Juan | All LLM and Jev calls, LangGraph graphs, prompts, team templates, learning, autonomy policy |
| `src/app/` | Mark | Telegram, FastAPI, Supabase, dashboard, real integrations (post, email, search, fetch), infra |
| `src/contract/` | Both | The models and Protocols described here. Nothing else |

- The **app never calls an LLM**. The **brain never touches Telegram or the database directly**.
- The brain reaches the outside world only through two interfaces the app implements: `Tools` (real actions and web access) and `Store` (data).
- The app talks to the brain only through the `Brain` interface.
- All models are Pydantic v2. Any change to this file or to `src/contract/` needs a quick OK from the other person.

## 2. Code map

Always import from the package root: `from contract import Approval, Say, Store`.

The folder tells you which side owns each file:

- **brain** (`brain_side/`): the brain implements or produces it; the app consumes it.
- **app** (`app_side/`): the app implements or produces it; the brain consumes it.
- **shared** (`models/`): data both sides use. Mostly the brain creates it and the app stores it.

| File | Side | Contains | Implemented / created by | Used by |
| --- | --- | --- | --- | --- |
| `brain_side/interface.py` | brain | `Brain`, `TemplateInfo` | Brain | App calls it |
| `brain_side/events.py` | brain | `Event` and its 11 types | Brain yields them | App renders them |
| `app_side/inputs.py` | app | `IncomingMessage`, `ApprovalDecision`, `PromotionResponse` | App builds them from Telegram / dashboard | Brain receives them |
| `app_side/tools.py` | app | `Tools`, `SearchResult`, `PageContent` | App | Brain calls it |
| `app_side/store.py` | app | `Store` | App (Supabase; brain has an in-memory fake) | Brain calls it |
| `models/team.py` | shared | `Persona`, `Team` | Brain | Both; app persists `Team` |
| `models/business.py` | shared | `BusinessProfile` | Brain (onboarding) | Both; app persists it |
| `models/autonomy.py` | shared | `AutonomyLevel`, `Trust` | Brain (dashboard demotion: app) | Both |
| `models/tasks.py` | shared | `TaskStatus`, `Task`, `Approval` | Brain | Both; app persists and shows them |
| `models/learning.py` | shared | `Lesson` | Brain (dashboard "forget": app) | Both |
| `models/actions.py` | shared | `PostSocial`, `SendEmail`, `PlannedAction`, `ActionResult`, `AuditEntry` | Brain plans actions and writes the audit log; app returns `ActionResult` | Both |

## 3. Conventions

- **IDs** are uuid4 strings, generated by whoever creates the record:
  - app: `business_id`, `action_id` (inside `Tools`)
  - brain: `team_id`, `task_id`, `approval_id`, `lesson_id`
- uuid4 strings fit Telegram's 64-byte `callback_data` (e.g. `"a:" + approval_id`).
- **Datetimes** are timezone-aware UTC.
- **`save_*`, `set_*` and `log_action` are upserts** keyed by the model's id.
- **`get_*` returns `None`** when the record doesn't exist; it never raises for "not found".
- **Thread ids** (LangGraph checkpoints): `"{business_id}:{team_id}"` for a team topic, `"{business_id}:company"` for the company-wide chat.
- **Errors:** brain methods never raise out of the iterator; they yield `Error`. `Tools` methods never raise for expected failures; they return `ok=False` with `error` (or `[]` for search).

## 4. Brain interface (`brain_side/interface.py`)

Juan implements, Mark calls. Every method except `list_templates` returns an async iterator of events. The brain streams as it works, so the app can show progress ("Sam is checking competitors…") instead of waiting 30 seconds in silence.

| Method | App calls it when | What the brain does |
| --- | --- | --- |
| `list_templates()` | Dashboard "Hire a team", `/hire` | Returns the hireable teams (sync, no events) |
| `start_onboarding(business_id)` | Founder sends `/start` in a new company group | Starts the chief-of-staff interview |
| `handle_message(msg)` | Founder writes in the General topic or a team topic (after debounce) | Continues onboarding, answers an `Ask`, or starts work |
| `hire_team(business_id, template)` | Dashboard button or `/hire <name>` | Creates the team and trust rows, yields `TeamHired`, then team onboarding |
| `resolve_approval(decision)` | Founder taps Approve / Reject, or sends the edited text | See the decision table in §7 |
| `respond_promotion(response)` | Founder answers a `PromotionOffer` | Updates the trust level (§8) |
| `undo_action(business_id, action_id)` | Founder taps Undo | Deletes the post if inside the window (§9) |

**`TemplateInfo`**: one hireable team, built from its YAML template.

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | `str` | Template id, the value passed to `hire_team` (`"marketing"`) |
| `display_name` | `str` | Shown to the founder (`"Marketing"`) |
| `description` | `str` | One line for the "Hire a team" card |
| `personas` | `list[Persona]` | Lead first, then specialists |
| `task_types` | `list[str]` | What this team can do (`["social_post", "newsletter"]`) |

The app maps Telegram topics to `team_id` (a topic's `message_thread_id` ↔ `team_id`) and groups to `business_id`. The brain never sees Telegram ids except `message_id`.

## 5. Stream lifecycle

- **`Ask` ends the stream.** The graph pauses. The founder's reply arrives as the next `handle_message` with the same `business_id` and `team_id`. Tapping a quick reply sends the button text as `text`.
- **`NeedsApproval` doesn't pause the graph.** The run ends after all drafts of the request are out. One stream may contain several `NeedsApproval` events (post + newsletter); each one is resolved independently, in any order, through `resolve_approval`.
- **`TeamHired` must be handled before the next event.** The app creates the forum topic and stores the mapping before rendering the next event: the lead's onboarding `Ask` follows right away on the new `team_id`.
- **One call at a time per thread.** The app never runs two brain calls concurrently for the same `(business_id, team_id)`. It queues them per thread.
- **Debounce.** The app waits 2–3 s after a founder message in a thread and joins consecutive messages with newlines into one `IncomingMessage`.
- **Edit flow is app-side.** Tapping Edit makes the app ask for the new version; the founder's next message in that topic becomes `ApprovalDecision.edited_text`. It is not sent to `handle_message`.
- **Interleaving.** Events of different tasks may interleave in one stream (parallel tasks later). Task-related events carry `task_id`.

## 6. Events (`brain_side/events.py`)

`Event` is a discriminated union on the `type` field. The app parses with `TypeAdapter(Event)` and renders each type. In every event, `team_id = None` means the company-wide chat (General topic).

**`Say`** (`type="say"`): a persona speaks in the chat.

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str \| None` | Topic to post in |
| `task_id` | `str \| None` | Task it's about, if any |
| `persona` | `Persona` | Who speaks; app renders "**Maya (Marketing lead):** …" |
| `text` | `str` | The message |

**`Progress`** (`type="progress"`): typing indicator plus a short status line; the app may edit one status message instead of posting many.

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str \| None` | Topic |
| `task_id` | `str \| None` | Task it's about, if any |
| `persona` | `Persona` | Who is working |
| `status` | `str` | "Sam is researching competitors…" |

**`Ask`** (`type="ask"`): a question to the founder. It ends the stream (§5).

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str \| None` | Topic |
| `persona` | `Persona` | Who asks |
| `question` | `str` | The question |
| `quick_replies` | `list[str]` | Optional buttons; tapping one sends its text as the reply |

**`NeedsApproval`** (`type="needs_approval"`): a draft waiting for Approve / Edit / Reject.

| Field | Type | Meaning |
| --- | --- | --- |
| `approval_id` | `str` | Pass back in `ApprovalDecision` |
| `team_id` | `str` | Topic |
| `task_id` | `str` | Task the draft belongs to |
| `task_type` | `str` | `"social_post"`, `"newsletter"`, `"invoice_reminder"` |
| `persona` | `Persona` | Who presents the draft (the lead) |
| `preview` | `str` | Human-readable: exactly what will be posted or sent |
| `planned_action` | `PlannedAction \| None` | What runs on approve; `None` for `draft_only` or task types without an action |
| `check_confidence` | `float` | 0–1, from the independent check; the app may show it |

**`ActionDone`** (`type="action_done"`): a real action happened.

| Field | Type | Meaning |
| --- | --- | --- |
| `action_id` | `str` | Pass to `undo_action` |
| `team_id` | `str` | Topic |
| `task_id` | `str` | Task it completed |
| `summary` | `str` | "Posted to Bluesky" |
| `url` | `str \| None` | Link to the post, if any |
| `autonomous` | `bool` | `True` if it ran without approval (earned autonomy) |
| `undo_until` | `datetime \| None` | Show an Undo button until then; `None` = not undoable |

**`ActionUndone`** (`type="action_undone"`): an undo succeeded; the app removes the Undo button.

| Field | Type | Meaning |
| --- | --- | --- |
| `action_id` | `str` | The original action that was undone |
| `team_id` | `str` | Topic |
| `summary` | `str` | "Deleted the Bluesky post" |

**`PromotionOffer`** (`type="promotion_offer"`): the team asks for more autonomy on one task type. The app shows Accept / Decline.

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str` | Topic |
| `task_type` | `str` | The task type to promote |
| `persona` | `Persona` | Who asks (the lead) |
| `current_level` | `AutonomyLevel` | Level now |
| `proposed_level` | `AutonomyLevel` | Next level up |
| `evidence` | `str` | "You approved my last 5 posts without edits" |

**`LessonLearned`** (`type="lesson_learned"`): the team learned something about this business. The app may show a "Forget" button that sets the lesson inactive.

| Field | Type | Meaning |
| --- | --- | --- |
| `lesson_id` | `str` | The saved `Lesson` |
| `team_id` | `str \| None` | Where to show it |
| `persona` | `Persona` | Who confirms it (the lead) |
| `text` | `str` | "No emojis in posts from now on" |
| `business_wide` | `bool` | `True` = shared with every team of this business |

**`TeamHired`** (`type="team_hired"`): the app creates the team's forum topic before the next event (§5).

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str` | New team; map it to the new topic |
| `template` | `str` | `"marketing"` |
| `display_name` | `str` | Topic name (`"Marketing"`) |
| `personas` | `list[Persona]` | Lead first |

**`OnboardingComplete`** (`type="onboarding_complete"`)

| Field | Type | Meaning |
| --- | --- | --- |
| `scope` | `"business" \| "team"` | Which onboarding finished |
| `team_id` | `str \| None` | The team, for `scope="team"` |

**`Error`** (`type="error"`)

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str \| None` | Topic |
| `message` | `str` | Safe to show the founder |
| `recoverable` | `bool` | `True` = the founder can retry or carry on |

## 7. App inputs (`app_side/inputs.py`)

**`IncomingMessage`**: one founder message (after debounce).

| Field | Type | Meaning |
| --- | --- | --- |
| `business_id` | `str` | From the Telegram group |
| `team_id` | `str \| None` | From the topic; `None` = General topic (onboarding, chief of staff) |
| `text` | `str` | Message text (debounced messages joined with newlines) |
| `message_id` | `str` | Telegram message id; recorded as the source of lessons learned from chat |
| `sent_at` | `datetime` | When the founder sent it |

**`ApprovalDecision`**

| Field | Type | Meaning |
| --- | --- | --- |
| `business_id` | `str` | Business |
| `approval_id` | `str` | From `NeedsApproval` |
| `decision` | `"approve" \| "edit" \| "reject"` | The founder's choice |
| `edited_text` | `str \| None` | Required for `edit`: the new post text, or the new email body |
| `reason` | `str \| None` | Optional for `reject`; with a reason the team revises and learns |

What each decision does (plain code in the brain, outside the graph):

| Decision | Effect |
| --- | --- |
| `approve` | Runs the planned action (if any), logs it, streak + 1, saves the draft as an approved example, may emit `PromotionOffer` |
| `edit` | Runs the action with `edited_text`, streak → 0, reflects on the diff → `LessonLearned` |
| `reject` + `reason` | Streak → 0, reflects → `LessonLearned`, the task is revised once with the reason → new `NeedsApproval` with a new `approval_id` (if revisions are left) |
| `reject`, no reason | Streak → 0, task → `REJECTED` |

Resolving an approval that isn't `pending` yields a recoverable `Error`.

**`PromotionResponse`**

| Field | Type | Meaning |
| --- | --- | --- |
| `business_id` | `str` | Business |
| `team_id` | `str` | From `PromotionOffer` |
| `task_type` | `str` | From `PromotionOffer` |
| `accepted` | `bool` | Accept or decline |

## 8. Autonomy (`models/autonomy.py`)

**`AutonomyLevel`**, in ladder order:

| Value | Meaning |
| --- | --- |
| `draft_only` | Never acts. Approve just marks the task done; the founder uses the draft themselves |
| `act_after_approval` | Acts only after Approve or Edit. Default for new teams |
| `act_and_report` | Acts without approval, then reports with `ActionDone` |
| `autonomous` | Acts without approval; `ActionDone` with `autonomous=True` (weekly summary out of scope) |

**`Trust`**: one row per `(team_id, task_type)`.

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str` | Team |
| `task_type` | `str` | Task type |
| `level` | `AutonomyLevel` | Current level |
| `approval_streak` | `int` | Approvals in a row without edits |
| `promote_after` | `int` | Clean approvals in a row before a promotion offer. Default 5, minimum 1; set by the founder on the dashboard |
| `updated_at` | `datetime` | Last change |

Rules (the brain owns the logic, the Store keeps the state):

- The brain creates one `Trust` row per task type on hire, at the template's `start_level`, streak 0, `promote_after` at its default.
- Approve without edits → streak + 1. Edit, reject or undo → streak 0. Actions taken without approval don't change the streak.
- **Promotion:** the streak reaches `promote_after`, the last `promote_after` approvals (`recent_approvals(limit=promote_after)`) all had `check_confidence ≥ 0.8`, and the level is below the template's cap → emit `PromotionOffer` for the next level up. Accept → level + 1, streak 0. Decline → streak 0 (no re-offer on the next approval).
- **Demotion:** the founder can demote anytime from the dashboard. The app writes the level directly (same as `Store.set_trust`) and resets the streak. The brain reads trust fresh every time; it never caches it.
- **Promotion threshold:** the founder can change `promote_after` from the dashboard. The app writes it through `Store.set_trust` and leaves the level and streak as they are.
- Finance never moves money at any level: no payment tool exists in `Tools`, and none may be added.

## 9. Tools (`app_side/tools.py`) and actions (`models/actions.py`)

Mark implements, the brain calls. Real actions run only after approval, or directly if the task type's level allows it. `web_search` and `fetch_page` are read-only and aren't audited.

| Method | What it does | Returns |
| --- | --- | --- |
| `post_social(business_id, text)` | Posts to the business's Bluesky | `ActionResult`; `external_id` = post URI |
| `delete_social(business_id, external_id)` | Deletes a post (undo) | `ActionResult` |
| `send_email(business_id, to, subject, body)` | Sends via Resend; `body` is plain text or simple markdown, the app converts | `ActionResult` |
| `web_search(query, k=5)` | Web search (Keenable or fallback) | `list[SearchResult]`, `[]` on failure |
| `fetch_page(url)` | Fetches a page as clean, model-ready text | `PageContent`, or `None` if unreachable |

**`PlannedAction`**: what will run on approval. A union on `tool`:

| Model | `tool` | Fields |
| --- | --- | --- |
| `PostSocial` | `"post_social"` | `text`: max 300 characters (Bluesky's limit is 300 graphemes; the brain stays under it) |
| `SendEmail` | `"send_email"` | `to`, `subject`, `body` |

**`ActionResult`**: returned by every action tool.

| Field | Type | Meaning |
| --- | --- | --- |
| `action_id` | `str` | Generated by the app for this call |
| `ok` | `bool` | Whether it worked |
| `url` | `str \| None` | Link to the result (post URL) |
| `external_id` | `str \| None` | Provider id needed for undo (Bluesky post URI) |
| `error` | `str \| None` | What went wrong, when `ok=False` |

**`SearchResult`**: `title`, `url`, `snippet`.

**`PageContent`**: `url`, `title` (`None` if the page has none), `text` (clean text; the app truncates to 20,000 characters).

**`AuditEntry`**: one row per real action, written by the brain through `Store.log_action`.

| Field | Type | Meaning |
| --- | --- | --- |
| `action_id` | `str` | From `ActionResult` |
| `business_id`, `team_id`, `task_id` | `str` | Where it came from |
| `tool` | `"post_social" \| "send_email" \| "delete_social"` | What ran |
| `summary` | `str` | "Posted to Bluesky" |
| `result` | `ActionResult` | Full tool result |
| `autonomous` | `bool` | `True` if no approval was needed |
| `approval_id` | `str \| None` | The approval that allowed it, if any |
| `undo_until` | `datetime \| None` | End of the undo window; `None` = not undoable |
| `undone_at` | `datetime \| None` | Set when the action was undone |
| `at` | `datetime` | When it ran |

**Undo:** 10 minutes, `post_social` only. Emails are never undoable (`undo_until=None`).
1. `undo_action` checks the window and that the action wasn't already undone. Outside the window it yields a recoverable `Error`.
2. It calls `delete_social`.
3. It logs the delete as its own `AuditEntry` and sets `undone_at` on the original.
4. It yields `ActionUndone`.

## 10. Store (`app_side/store.py`)

Mark implements (Supabase), the brain calls. The brain's tests and CLI use an in-memory fake.

| Method | Meaning |
| --- | --- |
| `get_profile(business_id)` / `save_profile(profile)` | The business profile. Saved once, when business onboarding completes |
| `save_team(team)` / `get_team(team_id)` / `list_teams(business_id)` | Hired teams |
| `get_trust(team_id, task_type)` / `set_trust(trust)` | Autonomy state; `None` before the brain creates the row on hire |
| `save_task(task)` / `get_task(task_id)` | Tasks, updated as they move through statuses |
| `save_approval(approval)` / `get_approval(approval_id)` | Approvals, pending and resolved |
| `recent_approvals(team_id, task_type, limit=5)` | Resolved approvals only (approved / edited / rejected), newest first. Used for approved examples and the promotion rule |
| `save_lesson(lesson)` | Adds or updates a lesson (also used to deactivate one) |
| `list_lessons(business_id, team_id, task_type=None)` | Active lessons only, newest first: business-wide lessons (`team_id` None) plus, if `team_id` is given, that team's lessons. With `task_type`, keeps lessons for that task type or for any (`task_type` None) |
| `log_action(entry)` / `get_action(action_id)` | Audit log |

The business itself (its row and the Telegram group mapping) is created by the app on `/start` and isn't part of the Store interface. The brain treats "business onboarding done" as `get_profile(...) is not None`. Partial onboarding answers live in the brain's checkpoint.

LangGraph checkpoints are the brain's own business. They live in their own tables, through the Postgres checkpointer on the same Supabase database, keyed by the thread ids from §3. This is the one place the brain touches the database. It uses `DATABASE_URL`, a direct or session-mode connection, because the transaction pooler breaks prepared statements. Tests and the CLI use an in-memory checkpointer.

## 11. Shared models

**`Persona`** (`models/team.py`): `name` ("Maya") and `role` ("Marketing lead").

**`Team`** (`models/team.py`): a team hired by one business.

| Field | Type | Meaning |
| --- | --- | --- |
| `team_id` | `str` | Generated by the brain on hire |
| `business_id` | `str` | Owner business |
| `template` | `str` | Template name (`"marketing"`) |
| `display_name` | `str` | `"Marketing"` |
| `onboarded` | `bool` | Team onboarding questions answered |
| `created_at` | `datetime` | Hire time |

**`BusinessProfile`** (`models/business.py`): the fixed business onboarding checklist. Required fields define "complete".

| Field | Type | Meaning |
| --- | --- | --- |
| `business_id` | `str` | Business |
| `name` | `str` | Business name (required) |
| `what_you_sell` | `str` | Services or products (required) |
| `customers` | `str` | Who buys (required) |
| `prices` | `str \| None` | Typical prices |
| `tone` | `str \| None` | Voice, e.g. "friendly, no jargon" |
| `main_clients` | `list[str]` | Client names (finance will need a richer `Client` model, v0.3) |
| `links` | `list[str]` | Website, socials |
| `extra` | `dict[str, str]` | Business-level odds and ends. Team-specific answers go to lessons, not here |

**`TaskStatus`** (`models/tasks.py`): `planned` → `in_progress` → `waiting_approval` → `done`, or `rejected` (founder rejected without a reason) or `failed` (something broke).

**`Task`** (`models/tasks.py`): one deliverable, the unit of capacity and pricing.

| Field | Type | Meaning |
| --- | --- | --- |
| `task_id` | `str` | Generated by the brain |
| `business_id`, `team_id` | `str` | Owner |
| `task_type` | `str` | `"social_post"` |
| `title` | `str` | Short label for the dashboard ("Launch post for Friday") |
| `brief` | `str` | What the lead asked the specialists to do |
| `status` | `TaskStatus` | Current status |
| `steps` | `list[str]` | Specialist ids in order, from the template (`["researcher", "writer"]`) |
| `current_step` | `int` | Index into `steps` |
| `revisions` | `int` | Revision rounds so far (2 included in one billable task) |
| `tokens_used` | `int` | LLM tokens spent, for the cost slide |
| `created_at`, `updated_at` | `datetime` | Timestamps |

**`Approval`** (`models/tasks.py`): one draft shown to the founder.

| Field | Type | Meaning |
| --- | --- | --- |
| `approval_id` | `str` | Generated by the brain |
| `business_id`, `team_id`, `task_id` | `str` | Where it belongs |
| `task_type` | `str` | Needed for trust updates |
| `preview` | `str` | The original draft as shown to the founder |
| `planned_action` | `PlannedAction \| None` | What runs on approve |
| `check_confidence` | `float` | 0–1, from the independent check |
| `status` | `"pending" \| "approved" \| "edited" \| "rejected"` | Starts `pending` |
| `edited_text` | `str \| None` | The founder's version, for `edited` |
| `reason` | `str \| None` | The founder's reason, for `rejected` |
| `created_at` | `datetime` | When it was shown |
| `resolved_at` | `datetime \| None` | When the founder decided |

**`Lesson`** (`models/learning.py`): something a team knows about this business.

| Field | Type | Meaning |
| --- | --- | --- |
| `lesson_id` | `str` | Generated by the brain |
| `business_id` | `str` | Business |
| `team_id` | `str \| None` | Team it belongs to; `None` = business-wide, shared by every team |
| `task_type` | `str \| None` | Only for this task type; `None` = all of the team's task types |
| `kind` | `"fact" \| "preference"` | Fact: "Posts on Bluesky and LinkedIn". Preference: "No emojis" |
| `key` | `str \| None` | Onboarding question key (`"channels"`), so re-answering replaces the old fact |
| `text` | `str` | The lesson, short and imperative |
| `source` | `"business_onboarding" \| "team_onboarding" \| "edit" \| "reject" \| "chat"` | Where it came from |
| `source_ref` | `str \| None` | The `approval_id` or `message_id` it came from |
| `active` | `bool` | `False` once the founder forgets it from the dashboard, or the brain replaces it |
| `created_at` | `datetime` | When it was learned |

## 12. Stubs and merge points (so nobody waits)

- **Mark:** `src/app/fake_brain.py` implements `Brain` and yields canned events for each method. It covers at least one `Ask`, two `NeedsApproval`, one `LessonLearned`, one `ActionDone` with an undo window and one `PromotionOffer`.
- **Juan:** `src/brain/cli.py` chats in the terminal, with `InMemoryStore` and `FakeTools` (print instead of posting) from `src/brain/fakes.py`.
- **Merge points:** Tue night, Wed night, Thu 12:30 (first full run), Thu night.

## 13. Decisions and open items

Decided (the open questions from v0.1, Oct 5):

1. **Undo:** 10 minutes, Bluesky posts only. Emails are never undoable.
2. **Who starts business onboarding:** the app, when the founder sends `/start` in the company group, by calling `start_onboarding`.
3. **Debounce:** yes, 2–3 s per thread in the app, plus a per-thread queue (§5).
4. **Cost logging:** `Task.tokens_used` only, no event.

Still open (Mark decides, app-side, no contract change expected):

- Dashboard: Lovable or Streamlit. Either way its endpoints or queries are app-internal.
- Search and fetch provider: Keenable (hackathon credits, has Search and Fetcher APIs) or DuckDuckGo as fallback.
- How each business connects its Bluesky account and email sender identity.
- Deployment target and how polling runs alongside FastAPI.

Later (contract v0.3, only if we get to finance): a `Client` model (name, email, amount owed, due date) and how the finance team fills it.
