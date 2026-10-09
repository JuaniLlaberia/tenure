import pytest

from brain.graphs.company import CHIEF_OF_STAFF, FIRST_QUESTION, MAX_TURNS
from brain.prompts.chief_of_staff import Extraction
from contract import (
    Ask,
    BusinessProfile,
    Error,
    IncomingMessage,
    OnboardingComplete,
    PageContent,
    Say,
)

BUSINESS_ID = "b1"

PROFILE = BusinessProfile(
    business_id=BUSINESS_ID, name="Juan's Studio", what_you_sell="Logos", customers="Cafés"
)

def general(text, message_id="m1"):
    return IncomingMessage(
        business_id=BUSINESS_ID,
        team_id=None,
        text=text,
        message_id=message_id,
        sent_at="2026-10-08T12:00:00Z",
    )

def extraction(**fields):
    facts = fields.pop("facts", [])
    return {"fields": fields, "facts": facts}

def extract_prompts(llm):
    return [str(call.messages) for call in llm.calls if call.schema is Extraction]

def question_prompts(llm):
    return [str(call.messages) for call in llm.calls if call.kind == "complete"]

@pytest.fixture
def clear_answers(jev):
    jev.answers["answer_clear"] = 0.9

async def onboard(brain, collect, llm, answers):
    """
    Runs /start, then one founder answer per scripted extraction.
    """
    llm.structured_responses["Extraction"] = [extraction(**fields) for _, fields in answers]
    events = [await collect(brain.start_onboarding(BUSINESS_ID))]
    for n, (text, _) in enumerate(answers, start=1):
        events.append(await collect(brain.handle_message(general(text, f"m{n}"))))
    return events

async def test_start_onboarding_asks_first_question(brain, collect, llm):
    events = await collect(brain.start_onboarding(BUSINESS_ID))

    assert [type(e) for e in events] == [Ask]
    assert events[0].team_id is None
    assert events[0].persona == CHIEF_OF_STAFF
    assert events[0].question == FIRST_QUESTION
    assert llm.calls == []

async def test_answers_fill_profile_across_turns(brain, collect, deps, llm, clear_answers):
    llm.structured_responses["Extraction"] = [
        extraction(name="Juan's Studio", what_you_sell="Café branding"),
        extraction(customers="Independent cafés", tone="warm"),
    ]
    await collect(brain.start_onboarding(BUSINESS_ID))

    first = await collect(brain.handle_message(general("Juan's Studio, café brands", "m1")))

    assert isinstance(first[-1], Ask)
    assert not any(isinstance(e, OnboardingComplete) for e in first)
    assert await deps.store.get_profile(BUSINESS_ID) is None

    second = await collect(brain.handle_message(general("Indie cafés, warm tone", "m2")))

    assert any(isinstance(e, OnboardingComplete) for e in second)
    profile = await deps.store.get_profile(BUSINESS_ID)
    assert (profile.name, profile.what_you_sell, profile.customers, profile.tone) == (
        "Juan's Studio",
        "Café branding",
        "Independent cafés",
        "warm",
    )

async def test_completion_saves_profile_and_facts(brain, collect, deps, llm, clear_answers):
    rounds = await onboard(
        brain,
        collect,
        llm,
        [
            (
                "Juan's Studio: café branding for indie cafés, friendly tone. Team of three.",
                {
                    "name": "Juan's Studio",
                    "what_you_sell": "Café branding",
                    "customers": "Independent cafés",
                    "tone": "friendly",
                    "facts": ["The studio has three people"],
                },
            ),
        ],
    )

    last = rounds[1]
    done = [e for e in last if isinstance(e, OnboardingComplete)]
    assert done and done[0].scope == "business" and done[0].team_id is None
    assert isinstance(last[-1], Say)
    assert last[-1].persona == CHIEF_OF_STAFF
    assert "marketing" in last[-1].text.lower()
    lessons = await deps.store.list_lessons(BUSINESS_ID, None)
    assert [lesson.text for lesson in lessons] == ["The studio has three people"]
    assert lessons[0].team_id is None
    assert (lessons[0].kind, lessons[0].source) == ("fact", "business_onboarding")

async def test_website_is_fetched_and_prefills(brain, collect, deps, llm, tools, clear_answers):
    tools.pages = {
        "https://juans.studio": PageContent(
            url="https://juans.studio",
            title="Juan's Studio",
            text="Brand design for cafés since 2019",
        )
    }

    await onboard(
        brain,
        collect,
        llm,
        [("Our site is https://juans.studio", {"name": "Juan's Studio"})],
    )

    assert ("fetch_page", {"url": "https://juans.studio"}) in tools.calls
    assert "Brand design for cafés since 2019" in extract_prompts(llm)[0]

async def test_website_link_is_kept_in_the_profile(brain, collect, deps, llm, clear_answers):
    await onboard(
        brain,
        collect,
        llm,
        [
            (
                "See https://juans.studio — café branding for indie cafés, warm tone",
                {
                    "name": "Juan's Studio",
                    "what_you_sell": "Café branding",
                    "customers": "Indie cafés",
                    "tone": "warm",
                },
            ),
        ],
    )

    assert "https://juans.studio" in (await deps.store.get_profile(BUSINESS_ID)).links

async def test_unreachable_website_does_not_break(brain, collect, llm, clear_answers):
    rounds = await onboard(
        brain,
        collect,
        llm,
        [("Our site is https://down.example", {"name": "Juan's Studio"})],
    )

    assert isinstance(rounds[1][-1], Ask)

async def test_unclear_answer_gets_follow_up(brain, collect, jev, llm):
    jev.answers["answer_clear"] = 0.2

    await onboard(brain, collect, llm, [("hmm, stuff", {})])

    follow_up = question_prompts(llm)[-1]
    assert "hmm, stuff" in follow_up
    assert "unclear" in follow_up.lower()

async def test_tone_is_asked_once_then_optional(brain, collect, deps, llm, clear_answers):
    rounds = await onboard(
        brain,
        collect,
        llm,
        [
            (
                "Juan's Studio, café branding for indie cafés",
                {
                    "name": "Juan's Studio",
                    "what_you_sell": "Café branding",
                    "customers": "Indie cafés",
                },
            ),
            ("No idea, really", {}),
        ],
    )

    assert isinstance(rounds[1][-1], Ask)
    assert "tone" in question_prompts(llm)[0].lower()
    assert any(isinstance(e, OnboardingComplete) for e in rounds[2])
    assert (await deps.store.get_profile(BUSINESS_ID)).tone is None

async def test_stops_asking_after_max_turns_when_required_are_filled(
    brain, collect, deps, llm, clear_answers
):
    required = {"name": "Juan's Studio", "what_you_sell": "Café branding", "customers": "Cafés"}
    answers = [("Juan's Studio, branding for cafés", required)] + [("pass", {})] * MAX_TURNS

    rounds = await onboard(brain, collect, llm, answers)

    def finished(events):
        return any(isinstance(e, OnboardingComplete) for e in events)

    completed = [i for i, events in enumerate(rounds) if finished(events)]
    assert completed and completed[0] <= MAX_TURNS

async def test_extraction_failure_does_not_break(brain, collect, llm, clear_answers):
    await collect(brain.start_onboarding(BUSINESS_ID))

    events = await collect(brain.handle_message(general("Juan's Studio")))

    assert not any(isinstance(e, Error) for e in events)
    assert isinstance(events[-1], Ask)

async def test_general_message_after_onboarding_gets_one_say(brain, collect, deps, llm):
    await deps.store.save_profile(PROFILE)

    events = await collect(brain.handle_message(general("What should I do next?")))

    assert [type(e) for e in events] == [Say]
    assert events[0].persona == CHIEF_OF_STAFF
    assert events[0].team_id is None
    assert "Juan's Studio" in question_prompts(llm)[0]

async def test_start_onboarding_when_already_onboarded(brain, collect, deps, llm):
    await deps.store.save_profile(PROFILE)

    events = await collect(brain.start_onboarding(BUSINESS_ID))

    assert [type(e) for e in events] == [Say]
    assert llm.calls == []

async def test_restart_resets_draft(brain, collect, llm, clear_answers):
    llm.structured_responses["Extraction"] = [extraction(name="Old Name"), extraction()]
    await collect(brain.start_onboarding(BUSINESS_ID))
    await collect(brain.handle_message(general("Old Name")))

    restarted = await collect(brain.start_onboarding(BUSINESS_ID))

    assert restarted[-1].question == FIRST_QUESTION
    config = {"configurable": {"thread_id": f"{BUSINESS_ID}:company"}}
    state = (await brain.company_graph.aget_state(config)).values
    assert not state["draft"].get("name")
