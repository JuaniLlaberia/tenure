from brain.check import run_check
from brain.prompts.critic import CriticReport
from brain.templates.registries import SocialPostOutput
from contract import BusinessProfile

PROFILE = BusinessProfile(
    business_id="b1",
    name="Juan's Studio",
    what_you_sell="Brand design for cafés",
    customers="Independent café owners",
    prices="Brand packages from $2,500",
    tone="warm, plain-spoken, no jargon",
)
POST = SocialPostOutput(text="We open our studio this Friday. Come say hi.")

async def test_state_includes_business_profile_when_given(deps, jev):
    jev.answers = {"passes_check": 0.9}

    await run_check(deps, deps.templates["marketing"], "social_post", POST, [], PROFILE)

    state = jev.calls[0][1]
    for text in ["Juan's Studio", "warm, plain-spoken, no jargon", "Brand packages from $2,500"]:
        assert text in state
    assert state.index("Juan's Studio") < state.index(POST.text)

async def test_critic_sees_business_profile(deps, jev, llm):
    jev.answers = {"passes_check": 0.1}
    llm.structured_responses["CriticReport"] = {"issues": ["Too formal"]}

    await run_check(deps, deps.templates["marketing"], "social_post", POST, [], PROFILE)

    critic = next(call for call in llm.calls if call.schema is CriticReport)
    assert "warm, plain-spoken, no jargon" in str(critic.messages)

async def test_without_profile_the_state_has_no_business_section(deps, jev):
    jev.answers = {"passes_check": 0.9}

    await run_check(deps, deps.templates["marketing"], "social_post", POST, [])

    assert "Business:" not in jev.calls[0][1]
