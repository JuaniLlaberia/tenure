import pytest
from pydantic import ValidationError

from brain.helpers.decide import MAX_STATE_CHARS, Decision, Question, decide

FEEDBACK = Question.yes_no(
    "Does the message contain feedback?", yes="It gives feedback", no="No feedback"
)
WORK = Question.yes_no("Does the message ask for work?", yes="It asks for work", no="No work")
ROUTE = Question(
    instructions="Which task type does the request need?",
    options={"social_post": "A post", "newsletter": "An email", "none": "Nothing"},
)

def fallback(**answers):
    return {
        "DecideFallback": {
            "answers": {
                key: {"choice": choice, "confidence": confidence}
                for key, (choice, confidence) in answers.items()
            }
        }
    }

def prompt_text(call):
    return "\n".join(str(message.get("content", "")) for message in call.messages)

def test_question_needs_two_options():
    with pytest.raises(ValidationError):
        Question(instructions="Pick", options={"only": "One option"})

def test_safe_is_last_option():
    assert FEEDBACK.safe == "no"
    assert ROUTE.safe == "none"

async def test_two_options_sent_as_noul(deps, jev):
    jev.answers = {"has_feedback": 0.9}
    await decide(deps, {"has_feedback": FEEDBACK}, "Stop using hashtags")
    model, _, questions = jev.calls[0]
    assert model == "typesafe/jev-1.13"
    assert questions["has_feedback"] == {
        "type": "noul",
        "instructions": "Does the message contain feedback?",
        "criteria": {"true": "It gives feedback", "false": "No feedback"},
    }

async def test_three_options_sent_as_choice(deps, jev):
    jev.answers = {"route": "social_post"}
    await decide(deps, {"route": ROUTE}, "Post about Friday")
    _, _, questions = jev.calls[0]
    assert questions["route"] == {
        "type": "choice",
        "instructions": ROUTE.instructions,
        "criteria": ROUTE.options,
    }

async def test_all_questions_in_one_jev_call(deps, jev):
    jev.answers = {"has_feedback": 0.9, "wants_work": 0.9}
    await decide(deps, {"has_feedback": FEEDBACK, "wants_work": WORK}, "Stop hashtags, post Friday")
    assert len(jev.calls) == 1
    assert set(jev.calls[0][2]) == {"has_feedback", "wants_work"}

async def test_noul_maps_to_first_option_above_half(deps, jev):
    jev.answers = {"has_feedback": 0.97}
    decisions = await decide(deps, {"has_feedback": FEEDBACK}, "Stop using hashtags")
    assert decisions["has_feedback"] == Decision(choice="yes", confidence=0.97, source="jev")

async def test_noul_maps_to_second_option_below_half(deps, jev):
    jev.answers = {"has_feedback": 0.2}
    decision = (await decide(deps, {"has_feedback": FEEDBACK}, "Post about Friday"))["has_feedback"]
    assert decision.choice == "no"
    assert decision.confidence == pytest.approx(0.8)

async def test_choice_uses_jev_confidence(deps, jev):
    jev.answers = {
        "route": {
            "type": "choice",
            "choice": "newsletter",
            "confidence": 0.88,
            "probabilities": {"social_post": 0.1, "newsletter": 0.85, "none": 0.05},
        }
    }
    decision = (await decide(deps, {"route": ROUTE}, "Send the monthly email"))["route"]
    assert decision == Decision(choice="newsletter", confidence=0.88, source="jev")

async def test_jev_error_falls_back_to_llm(deps, jev, llm):
    jev.fail = True
    llm.structured_responses.update(fallback(has_feedback=("yes", 0.8)))
    decisions = await decide(deps, {"has_feedback": FEEDBACK}, "Stop using hashtags")
    assert decisions["has_feedback"] == Decision(choice="yes", confidence=0.8, source="llm")
    assert llm.calls[0].model == deps.settings.model_decide_fallback

async def test_missing_answer_falls_back_for_that_question_only(deps, jev, llm):
    jev.answers = {"has_feedback": 0.9}
    llm.structured_responses.update(fallback(wants_work=("no", 0.75)))
    decisions = await decide(
        deps, {"has_feedback": FEEDBACK, "wants_work": WORK}, "Stop using hashtags"
    )
    assert decisions["has_feedback"].source == "jev"
    assert decisions["wants_work"] == Decision(choice="no", confidence=0.75, source="llm")
    prompt = prompt_text(llm.calls[0])
    assert WORK.instructions in prompt
    assert FEEDBACK.instructions not in prompt

async def test_invalid_choice_falls_back(deps, jev, llm):
    jev.answers = {"route": "tweet"}
    llm.structured_responses.update(fallback(route=("social_post", 0.7)))
    decision = (await decide(deps, {"route": ROUTE}, "Post about Friday"))["route"]
    assert decision == Decision(choice="social_post", confidence=0.7, source="llm")

async def test_both_fail_returns_safe_option(deps, jev, llm):
    jev.fail = True
    llm.fail = True
    decisions = await decide(deps, {"has_feedback": FEEDBACK, "route": ROUTE}, "Anything")
    assert decisions["has_feedback"] == Decision(choice="no", confidence=0.0, source="default")
    assert decisions["route"] == Decision(choice="none", confidence=0.0, source="default")

async def test_invalid_fallback_choice_returns_safe_option(deps, jev, llm):
    jev.fail = True
    llm.structured_responses.update(fallback(has_feedback=("maybe", 0.9)))
    decision = (await decide(deps, {"has_feedback": FEEDBACK}, "Anything"))["has_feedback"]
    assert decision == Decision(choice="no", confidence=0.0, source="default")

async def test_confidence_is_clamped(deps, jev, llm):
    jev.answers = {"route": {"type": "choice", "choice": "newsletter", "confidence": 1.4}}
    llm.structured_responses.update(fallback(has_feedback=("yes", -0.2)))
    decisions = await decide(deps, {"route": ROUTE, "has_feedback": FEEDBACK}, "Anything")
    assert decisions["route"].confidence == 1.0
    assert decisions["has_feedback"].confidence == 0.0

def test_accepts_needs_option_and_threshold():
    assert Decision(choice="yes", confidence=0.7, source="jev").accepts("yes", 0.7)
    assert not Decision(choice="yes", confidence=0.6, source="jev").accepts("yes", 0.7)
    assert not Decision(choice="no", confidence=0.9, source="jev").accepts("yes", 0.7)

async def test_long_state_is_truncated_keeping_the_end(deps, jev):
    jev.answers = {"has_feedback": 0.9}
    await decide(deps, {"has_feedback": FEEDBACK}, "a" * 100_000 + "THE END")
    _, state, _ = jev.calls[0]
    assert len(state) <= MAX_STATE_CHARS
    assert state.startswith("…")
    assert state.endswith("THE END")

async def test_short_state_is_sent_as_is(deps, jev):
    jev.answers = {"has_feedback": 0.9}
    await decide(deps, {"has_feedback": FEEDBACK}, "Stop using hashtags")
    assert jev.calls[0][1] == "Stop using hashtags"

async def test_tokens_add_jev_and_fallback(deps, jev, llm):
    jev.answers = {"has_feedback": 0.9}
    jev.input_tokens = 100
    llm.tokens_per_call = 10
    llm.structured_responses.update(fallback(wants_work=("no", 0.75)))
    decisions = await decide(deps, {"has_feedback": FEEDBACK, "wants_work": WORK}, "Anything")
    assert decisions.tokens == 110
