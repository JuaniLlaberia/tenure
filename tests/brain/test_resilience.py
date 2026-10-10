from datetime import UTC, datetime

import httpx
import pytest

from brain.common import OUT_OF_CREDITS, OutOfCredits
from brain.deps import Settings
from brain.helpers.images import OpenRouterImages
from brain.helpers.jev import OpenRouterJev
from brain.helpers.llm import OpenRouterLLM
from brain.prompts.lead import CANT_DELETE, SendTime
from contract import (
    ActionDone,
    ApprovalDecision,
    Ask,
    AutonomyLevel,
    BusinessProfile,
    Error,
    NeedsApproval,
    Say,
)

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

async def asked_where(brain, collect, script, team, message):
    script(task_types=("social_post", "newsletter"), named=0.1)
    events = await collect(brain.handle_message(message(team, "Start a campaign for our launch")))
    assert isinstance(events[-1], Ask)

async def test_never_mind_drops_the_request(brain, collect, jev, script, team, message):
    await asked_where(brain, collect, script, team, message)
    jev.answers["cancels"] = lambda state: 0.9 if "never mind" in state else 0.1

    events = await collect(brain.handle_message(message(team, "never mind", "m2")))

    assert [say.text for say in of(events, Say)] == ["Okay, I've dropped that request."]
    assert not of(events, NeedsApproval) and not of(events, Ask)
    script(named=0.9)
    after = await collect(brain.handle_message(message(team, "Make a Bluesky post", "m3")))
    assert of(after, NeedsApproval)

async def test_a_new_request_sets_the_question_aside(brain, collect, jev, script, team, message):
    await asked_where(brain, collect, script, team, message)
    script(task_types=("social_post",), named=0.9)
    jev.answers["new_request"] = lambda state: 0.9 if "pricing" in state else 0.1

    events = await collect(brain.handle_message(message(team, "Post about our pricing", "m2")))

    assert of(events, Say)[0].text == (
        "I'll set aside my question about “Start a campaign for our launch” and start on this."
    )
    (needs,) = of(events, NeedsApproval)
    assert needs.task_type == "social_post"

async def test_an_answer_still_answers(brain, collect, jev, script, team, message):
    await asked_where(brain, collect, script, team, message)
    jev.answers["needs_newsletter"] = 0.1

    events = await collect(brain.handle_message(message(team, "Only Bluesky", "m2")))

    assert [d.task_type for d in of(events, NeedsApproval)] == ["social_post"]
    assert not any("set aside" in say.text or "dropped" in say.text for say in of(events, Say))

async def test_feedback_on_the_pending_draft_revises_it(
    brain, collect, deps, jev, script, team, message
):
    script()
    first = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    (draft,) = of(first, NeedsApproval)
    jev.answers["is_draft_feedback"] = lambda state: 0.9 if "shorter" in state else 0.1

    events = await collect(brain.handle_message(message(team, "make it shorter", "m2")))

    (again,) = of(events, NeedsApproval)
    assert again.task_id == draft.task_id and again.approval_id != draft.approval_id
    old = await deps.store.get_approval(draft.approval_id)
    assert (old.status, old.reason) == ("rejected", "make it shorter")
    assert (await deps.store.get_task(draft.task_id)).revisions == 1

async def test_a_new_request_with_a_draft_pending_is_a_new_request(
    brain, collect, deps, jev, script, team, message
):
    script()
    first = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    (draft,) = of(first, NeedsApproval)

    events = await collect(brain.handle_message(message(team, "Now a post about pricing", "m2")))

    (other,) = of(events, NeedsApproval)
    assert other.task_id != draft.task_id
    assert (await deps.store.get_approval(draft.approval_id)).status == "pending"

def reply_prompt(llm):
    calls = [call for call in llm.calls if call.kind == "complete"]
    return calls[-1].messages[0]["content"]

async def test_the_lead_never_claims_a_delete(brain, collect, llm, script, team, message):
    script(work=0.1)

    await collect(brain.handle_message(message(team, "Delete my last post")))

    prompt = reply_prompt(llm)
    assert "Never say you posted, sent, scheduled or deleted anything" in prompt
    assert CANT_DELETE in prompt

async def test_work_for_an_unhired_team_points_to_hiring_it(
    brain, collect, llm, script, team, message
):
    script(work=0.1)

    await collect(brain.handle_message(message(team, "Make me a logo")))

    prompt = reply_prompt(llm)
    assert "is Design's work. Hire them with /hire design, then ask Iris in the Design topic." in (
        prompt
    )

async def test_work_for_a_hired_team_points_to_its_topic(
    brain, collect, llm, make_team, script, team, message
):
    await make_team(template="design")
    script(work=0.1)

    await collect(brain.handle_message(message(team, "Make me a logo")))

    prompt = reply_prompt(llm)
    assert "is Design's work. Ask Iris in the Design topic." in prompt
    assert "/hire design" not in prompt

async def asked_for_instagram(brain, collect, jev, script, team, message):
    script()
    jev.answers["other_channel"] = lambda state: 0.9 if "Instagram" in state else 0.1
    events = await collect(brain.handle_message(message(team, "Make an Instagram post")))
    return events

async def test_another_channel_offers_a_bluesky_post(brain, collect, jev, script, team, message):
    events = await asked_for_instagram(brain, collect, jev, script, team, message)

    (ask,) = of(events, Ask)
    assert ask.question == (
        "I can only post to Bluesky and send emails for now. Want this as a Bluesky post instead?"
    )
    assert ask.quick_replies == ["Yes, a Bluesky post", "No"]
    assert not of(events, NeedsApproval)

async def test_yes_makes_a_bluesky_post(brain, collect, jev, script, team, message):
    await asked_for_instagram(brain, collect, jev, script, team, message)
    jev.answers["wants_bluesky"] = lambda state: 0.9 if "Yes, a Bluesky post" in state else 0.1

    events = await collect(brain.handle_message(message(team, "Yes, a Bluesky post", "m2")))

    assert [d.task_type for d in of(events, NeedsApproval)] == ["social_post"]

async def test_no_drops_the_other_channel_request(brain, collect, jev, script, team, message):
    await asked_for_instagram(brain, collect, jev, script, team, message)
    jev.answers["wants_bluesky"] = 0.1

    events = await collect(brain.handle_message(message(team, "No", "m2")))

    assert [say.text for say in of(events, Say)] == ["Okay, I've dropped that request."]
    assert not of(events, NeedsApproval)

FRIDAY_6PM = {"date": "2026-10-09", "time": "18:00"}

async def timed(deps, jev, llm, script, found=FRIDAY_6PM):
    script()
    await deps.store.save_profile(
        BusinessProfile(
            business_id="b1",
            name="Juan's Studio",
            what_you_sell="Logos",
            customers="Cafés",
            extra={"timezone": "America/Argentina/Buenos_Aires"},
        )
    )
    jev.answers["names_send_time"] = lambda state: 0.9 if "Friday at 6" in state else 0.1
    llm.structured_responses["SendTime"] = found

async def test_a_named_time_goes_on_the_draft(
    brain, collect, deps, jev, llm, script, team, message
):
    await timed(deps, jev, llm, script)

    events = await collect(brain.handle_message(message(team, "Post this Friday at 6 PM")))

    (needs,) = of(events, NeedsApproval)
    assert needs.send_at == datetime(2026, 10, 9, 21, 0, tzinfo=UTC)
    assert (await deps.store.get_approval(needs.approval_id)).send_at == needs.send_at
    (call,) = [c for c in llm.calls if c.schema is SendTime]
    assert "Thursday 2026-10-08 09:00" in str(call.messages)

async def test_without_a_time_there_is_no_send_time(brain, collect, script, team, message):
    script()

    events = await collect(brain.handle_message(message(team, "Post about our launch")))

    assert of(events, NeedsApproval)[0].send_at is None

async def test_a_time_in_the_past_is_ignored(
    brain, collect, deps, jev, llm, script, team, message
):
    await timed(deps, jev, llm, script, found={"date": "2026-10-01", "time": "18:00"})

    events = await collect(brain.handle_message(message(team, "Post this Friday at 6 PM")))

    assert of(events, NeedsApproval)[0].send_at is None

async def test_a_timed_draft_waits_for_approval_even_when_autonomous(
    brain, collect, deps, jev, llm, script, make_team, message
):
    team = await make_team(levels={"social_post": AutonomyLevel.AUTONOMOUS})
    await timed(deps, jev, llm, script)

    events = await collect(brain.handle_message(message(team, "Post this Friday at 6 PM")))

    assert of(events, NeedsApproval) and not of(events, ActionDone)

async def test_a_revision_keeps_the_send_time(
    brain, collect, deps, jev, llm, script, team, message
):
    await timed(deps, jev, llm, script)
    events = await collect(brain.handle_message(message(team, "Post this Friday at 6 PM")))
    (needs,) = of(events, NeedsApproval)
    decision = ApprovalDecision(
        business_id="b1", approval_id=needs.approval_id, decision="reject", reason="Shorter"
    )

    events = await collect(brain.resolve_approval(decision))

    (again,) = of(events, NeedsApproval)
    assert again.send_at == needs.send_at
