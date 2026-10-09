# Stage 8: Company graph (business onboarding)

**Status:** done; tests waiting for Juan's review
**Depends on:** stage 6 (facade)
**Spec:** [brain-engine.md](../specs/brain-engine.md) §3.2 (company graph), §2 (interrupt rule); [CONTRACT.md](../CONTRACT.md) §10 (onboarding done = profile exists), §11 (`BusinessProfile`)

## Goal

The chief of staff interviews the founder in the General topic until the business profile checklist is complete, prefilled from their website when they paste one. Afterwards, General-topic messages get one short chief-of-staff reply.

## Files

| File | Contains |
| --- | --- |
| `src/brain/graphs/company.py` | `ProfileDraft`, `CompanyState`, `build_company_graph()`, `CHIEF_OF_STAFF` persona |
| `src/brain/prompts/chief_of_staff.py` | the question, extract and reply messages, `Extraction` schema |
| `src/brain/brain.py` | `start_onboarding`, `handle_message(team_id=None)` |

## Interfaces

```python
# graphs/company.py
CHIEF_OF_STAFF = Persona(name="Alex", role="Chief of staff")    # placeholder name
REQUIRED = ("name", "what_you_sell", "customers")
MAX_TURNS = 8

class ProfileDraft(BaseModel):          # every BusinessProfile field, all optional
    name: str | None = None
    what_you_sell: str | None = None
    customers: str | None = None
    prices: str | None = None
    tone: str | None = None
    main_clients: list[str] = []
    links: list[str] = []
    extra: dict[str, str] = {}
    def missing(self) -> list[str]: ...  # required fields still empty, plus tone (asked once, optional)
    def to_profile(self, business_id: str) -> BusinessProfile: ...

class CompanyState(TypedDict, total=False):
    business_id: str
    draft: ProfileDraft
    facts: list[str]                     # extra things learned, saved as business lessons at the end
    question: str | None
    answer: str | None
    turns: int
    message: str | None                  # a General-topic message after onboarding

def build_company_graph(deps: Deps, checkpointer) -> CompiledStateGraph: ...

# prompts/chief_of_staff.py
class Extraction(BaseModel):
    fields: ProfileDraft                 # only what the answer says
    facts: list[str] = []                # other useful facts, short
```

### Graph

```
entry ──profile exists──▶ chief_reply ──▶ END
  │ no
  ▼
question ──▶ wait ──▶ extract ──▶ complete? ──yes──▶ finish ──▶ END
   ▲                                │ no
   └────────────────────────────────┘
```

| Node | Does |
| --- | --- |
| `entry` | `store.get_profile` → exists → `chief_reply`. `start_onboarding` input has `restart=True` → resets the draft |
| `question` | first turn: fixed text, no LLM ("Hi, I'm Alex… What's the business called, and what do you sell? You can also paste your website."). Later turns: one LLM `complete()` asking about `draft.missing()`, or a follow-up if the last answer was unclear |
| `wait` | `interrupt(Ask(question))` only (interrupt rule) |
| `extract` | URLs in the answer → `tools.fetch_page` → page text added to the extract input; LLM `structured(Extraction)` merges into the draft (lists append, scalars only fill or replace when the answer gives them). `decide()` `answer_clear` (`clear`, `unclear`) on the last answer; unclear → the next question is a follow-up. `turns + 1` |
| `complete?` | plain code: all `REQUIRED` filled and (tone asked or `turns ≥ MAX_TURNS`) |
| `finish` | `save_profile`, each fact → business-wide `fact` lesson (`source="business_onboarding"`), `OnboardingComplete(scope="business", team_id=None)`, chief-of-staff `Say` suggesting to hire marketing |
| `chief_reply` | one LLM `complete()` in the chief-of-staff persona, with the profile and team list → one `Say` |

Thread `{business_id}:company`. All events have `team_id=None`.

### Facade

- `start_onboarding(business_id)`: runs the company graph with `{"business_id", "restart": True}`. A profile already exists → one `Say` ("We're already set up…").
- `handle_message` with `team_id=None`: pending interrupt → resume with the text; else run with `{"business_id", "message": text}`.

## Tests

`tests/brain/test_company_graph.py` (through the facade)

- `test_start_onboarding_asks_first_question`: one `Ask` from the chief of staff, `team_id=None`, no LLM call.
- `test_answers_fill_profile_across_turns`: scripted `Extraction`s; no `OnboardingComplete` while a required field is missing.
- `test_completion_saves_profile_and_facts`: profile in the Store, facts as business-wide lessons, `OnboardingComplete(scope="business")`, a `Say`.
- `test_website_is_fetched_and_prefills`: an answer with `https://example.com` → `FakeTools` fetched it; the extract prompt contains the page text.
- `test_unreachable_website_does_not_break`: `fetch_page` → `None`; the interview continues.
- `test_unclear_answer_gets_follow_up`: `answer_clear` unclear → the next question is a follow-up about the same field.
- `test_stops_asking_after_max_turns_when_required_are_filled`
- `test_general_message_after_onboarding_gets_one_say`
- `test_start_onboarding_when_already_onboarded`
- `test_restart_resets_draft`

## Done when

- [x] All tests above pass; earlier stages still pass
- [x] `ruff check` passes
- [ ] Juan has reviewed the tests

## Log

- Oct 9: implemented under `/goal start implementation for stage 8`. Tests written first and run red (13 of 13 failed on stubs), then implemented: 283 passed, `ruff check` clean.
- One test was wrong and fixed before review: `test_answers_fill_profile_across_turns` asserted "no profile yet" after both answers. It now checks after the first answer, as intended.
- Tests added beyond the list above: `test_website_link_is_kept_in_the_profile`, `test_tone_is_asked_once_then_optional` (replaces the vaguer "max turns" idea; `test_stops_asking_after_max_turns_when_required_are_filled` is kept too) and `test_extraction_failure_does_not_break`.
- Design details not in the interfaces above:
  - `ProfileDraft` and `Extraction` live in `prompts/chief_of_staff.py`. The graph state keeps the draft as a JSON dict (checkpoint-safe, like stage 6).
  - Completeness: the 3 required fields plus tone either given, asked once (`asked_tone`), or `turns ≥ MAX_TURNS`.
  - "Unclear" needs both `answer_clear` below the threshold *and* nothing new extracted, so a terse but useful answer isn't questioned again.
  - If the question LLM fails there's a fixed `fallback_question()`; if extraction fails nothing is learned and the interview goes on.
  - The facade: `start_onboarding` answers with one `Say` if a profile exists, else starts the company graph with `restart=True` (new input on the paused thread drops the old question and resets the draft). `handle_message(team_id=None)` resumes a pending `Ask` or starts a run with the message. Both graphs share one checkpointer; threads are `{business_id}:company` and `{business_id}:{team_id}`. `_stream()` turns any graph's stream into Events.
- Live run (real DeepSeek and Jev, fake tools serving a website):
  - First prompt version: the website gave name and offer, but prices, clients and tone were missed; tone stayed empty after being asked once.
  - After telling the extraction to fill *every* field the website supports, one answer ("Here's our site: https://juans.studio") filled name, offer, customers, prices, tone, both named clients and `extra.location`, and onboarding completed.
  - A General-topic message afterwards got one sensible chief-of-staff reply pointing to `/hire marketing`.
