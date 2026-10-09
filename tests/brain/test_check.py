from datetime import UTC, datetime

import pytest

from brain.check import hard_validate, run_check
from brain.prompts.critic import CriticReport
from brain.templates.registries import EmailOutput, ReportOutput, SocialPostOutput
from contract import Lesson

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

GOOD_POST = SocialPostOutput(text="We launch Friday. Come say hi at the studio.")
GOOD_EMAIL = EmailOutput(to="list@b.co", subject="We launch Friday", body="Join us on Friday.")
GOOD_REPORT = ReportOutput(summary="Two rivals cut prices.", sources=["https://a.co/news"])

def lesson(text, task_type=None):
    return Lesson(
        lesson_id=text,
        business_id="b1",
        team_id="t1",
        task_type=task_type,
        kind="preference",
        text=text,
        source="chat",
        created_at=NOW,
    )

LESSONS = [lesson("No hashtags in posts"), lesson("Sign posts as Juan")]

@pytest.fixture
def marketing(deps):
    return deps.templates["marketing"]

def jev_state(jev):
    return jev.calls[0][1]

def critic_prompt(llm):
    call = next(c for c in llm.calls if c.schema is CriticReport)
    return "\n".join(str(m.get("content", "")) for m in call.messages)

async def test_post_over_300_chars_fails_hard(deps, jev, llm, marketing):
    result = await run_check(
        deps, marketing, "social_post", SocialPostOutput(text="x" * 301), LESSONS
    )

    assert not result.passed
    assert result.confidence == 0.0
    assert any("300" in item for item in result.feedback)
    assert jev.calls == []
    assert llm.calls == []

def test_email_without_subject_fails_hard():
    assert hard_validate("email", EmailOutput(to="list@b.co", subject=" ", body="Hi"))
    assert hard_validate("email", EmailOutput(to="list@b.co", subject="Hi", body=""))
    assert hard_validate("email", EmailOutput(to="not-an-address", subject="Hi", body="Hi"))

@pytest.mark.parametrize("placeholder", ["[Name]", "{{date}}", "<Company>", "TODO", "Lorem ipsum"])
def test_placeholder_fails_hard(placeholder):
    post = SocialPostOutput(text=f"Hi {placeholder}, see you Friday")
    errors = hard_validate("social_post", post)
    assert errors
    assert any(placeholder.lower() in item.lower() for item in errors)

def test_markdown_link_is_not_a_placeholder():
    text = "We launch Friday. [Read more](https://b.co/launch)"
    assert hard_validate("social_post", SocialPostOutput(text=text)) == []

def test_report_without_sources_fails_hard():
    assert hard_validate("report", ReportOutput(summary="Rivals cut prices.", sources=[]))
    assert hard_validate("report", ReportOutput(summary="", sources=["https://a.co"]))

def test_valid_drafts_pass_hard_validation():
    assert hard_validate("social_post", GOOD_POST) == []
    assert hard_validate("email", GOOD_EMAIL) == []
    assert hard_validate("report", GOOD_REPORT) == []

async def test_pass_above_threshold(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.9}

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert result.passed
    assert result.confidence == pytest.approx(0.9)
    assert result.feedback == []
    assert llm.calls == []
    assert jev.calls[0][2]["passes_check"]["type"] == "noul"

async def test_pass_below_threshold_is_revise(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.6}
    llm.structured_responses["CriticReport"] = {"issues": ["Too vague about the date"]}

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert not result.passed
    assert result.confidence == pytest.approx(0.6)
    assert any(call.schema is CriticReport for call in llm.calls)

async def test_revise_uses_critic_issues_as_feedback(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.1}
    llm.structured_responses["CriticReport"] = {
        "issues": ["Uses hashtags, which breaks 'No hashtags in posts'"]
    }

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert not result.passed
    assert result.feedback == ["Uses hashtags, which breaks 'No hashtags in posts'"]

async def test_revise_confidence_is_probability_of_pass(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.1}
    llm.structured_responses["CriticReport"] = {"issues": ["Too salesy"]}

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert result.confidence == pytest.approx(0.1)

async def test_decide_failure_is_revise_with_zero_confidence(deps, jev, llm, marketing):
    jev.fail = True
    llm.structured_responses["CriticReport"] = {"issues": ["Couldn't verify the draft"]}

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert not result.passed
    assert result.confidence == 0.0

async def test_critic_failure_falls_back_to_rules(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.1}
    llm.fail = True

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert not result.passed
    joined = " ".join(result.feedback)
    assert "matches the business tone" in joined
    assert "no invented facts or prices" in joined

async def test_empty_critic_issues_fall_back_to_rules(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.1}
    llm.structured_responses["CriticReport"] = {"issues": []}

    result = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    assert "matches the business tone" in " ".join(result.feedback)

async def test_state_contains_rules_lessons_and_draft(deps, jev, marketing):
    jev.answers = {"passes_check": 0.9}

    await run_check(deps, marketing, "newsletter", GOOD_EMAIL, LESSONS)

    state = jev_state(jev)
    for text in ["clear subject line", "one call to action", "No hashtags in posts"]:
        assert text in state
    assert GOOD_EMAIL.subject in state
    assert GOOD_EMAIL.body in state

async def test_state_excludes_lead_and_specialist_instructions(deps, jev, marketing):
    jev.answers = {"passes_check": 0.9}

    await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    state = jev_state(jev)
    assert marketing.lead.instructions not in state
    for persona in marketing.personas():
        assert persona.name not in state

async def test_critic_prompt_contains_rules_lessons_and_draft(deps, jev, llm, marketing):
    jev.answers = {"passes_check": 0.1}
    llm.structured_responses["CriticReport"] = {"issues": ["Too salesy"]}

    await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)

    prompt = critic_prompt(llm)
    for text in ["matches the business tone", "Sign posts as Juan", GOOD_POST.text]:
        assert text in prompt
    assert marketing.lead.instructions not in prompt
    assert llm.calls[0].model == deps.settings.model_critic

async def test_tokens_are_counted(deps, jev, llm, marketing):
    jev.input_tokens = 100
    llm.tokens_per_call = 10
    jev.answers = {"passes_check": 0.9}
    passed = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)
    assert passed.tokens == 100

    jev.answers = {"passes_check": 0.1}
    llm.structured_responses["CriticReport"] = {"issues": ["Too salesy"]}
    revised = await run_check(deps, marketing, "social_post", GOOD_POST, LESSONS)
    assert revised.tokens == 110

async def test_critic_uses_its_own_model_and_leaves_reasoning_to_it(deps, llm, jev):
    jev.answers["passes_check"] = 0.1
    llm.structured_responses["CriticReport"] = {"issues": ["Too vague about the date"]}
    template = deps.templates["marketing"]
    await run_check(deps, template, "social_post", SocialPostOutput(text="Soon!"), [])
    (call,) = [c for c in llm.calls if c.schema is CriticReport]
    assert call.model == deps.settings.model_critic == "anthropic/claude-sonnet-5.5"
    assert call.reasoning is None
