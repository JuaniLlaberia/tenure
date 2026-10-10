import httpx
import pytest

from brain.common import OUT_OF_CREDITS, OutOfCredits
from brain.deps import Settings
from brain.helpers.images import OpenRouterImages
from brain.helpers.jev import OpenRouterJev
from brain.helpers.llm import OpenRouterLLM
from contract import Error, NeedsApproval, Say

SETTINGS = Settings(openrouter_api_key="sk-test-key")
NO_CREDITS = {"error": {"code": 402, "message": "Insufficient credits. Add more using /credits"}}

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def answering(status, body):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json=body))
    return httpx.AsyncClient(transport=transport)

@pytest.fixture
async def team(make_team):
    return await make_team()

async def test_the_llm_client_reports_out_of_credits():
    llm = OpenRouterLLM(SETTINGS, http_client=answering(402, NO_CREDITS))

    with pytest.raises(OutOfCredits):
        await llm.complete("deepseek/deepseek-v4-flash-0731", [{"role": "user", "content": "Hi"}])

async def test_jev_and_images_report_out_of_credits():
    transport = httpx.MockTransport(lambda _: httpx.Response(402, json=NO_CREDITS))
    jev = OpenRouterJev(SETTINGS, transport=transport)
    images = OpenRouterImages(SETTINGS, transport=transport)

    with pytest.raises(OutOfCredits):
        await jev.ask("typesafe/jev-1.13", "state", {})
    with pytest.raises(OutOfCredits):
        await images.generate("google/gemini-3.1-flash-image", "A cat")

def out_of_credits(*_):
    raise OutOfCredits("deepseek/deepseek-v4-flash-0731")

async def test_out_of_credits_mid_run_ends_with_one_clear_error(
    brain, collect, llm, script, team, message
):
    script()
    llm.structured_responses["LeadPlan"] = out_of_credits

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    (error,) = of(events, Error)
    assert error.message == OUT_OF_CREDITS and error.recoverable
    assert not of(events, NeedsApproval)

async def test_jev_out_of_credits_never_falls_back_to_another_model(
    brain, collect, jev, llm, script, team, message
):
    script()
    jev.answers["has_feedback"] = out_of_credits

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    assert [e.message for e in of(events, Error)] == [OUT_OF_CREDITS]
    assert llm.calls == []
    assert not of(events, Say)
