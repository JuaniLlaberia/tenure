# Stage 8: Company graph (business onboarding)

**Status:** not started
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

- [ ] All tests above pass; earlier stages still pass
- [ ] `ruff check` passes

## Log

_Empty._
