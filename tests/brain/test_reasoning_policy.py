import pytest
from tests.brain.conftest import NOW

from brain.learning import ReflectOutput
from brain.prompts.chief_of_staff import Extraction
from contract import ApprovalDecision, IncomingMessage, NeedsApproval

@pytest.fixture
async def team(make_team):
    return await make_team()

def reasoning_by_kind(llm) -> dict[str, set]:
    """
    For each call: the schema name (structured) or "complete", mapped to the reasoning flags seen.
    """
    seen: dict[str, set] = {}
    for call in llm.calls:
        name = call.schema.__name__ if call.schema else "complete"
        seen.setdefault(name, set()).add(call.reasoning)
    return seen

async def test_onboarding_never_reasons(brain, collect, llm, jev):
    jev.answers["answer_clear"] = 0.9
    llm.structured_responses["Extraction"] = {"fields": {"name": "Bright"}, "facts": []}
    await collect(brain.start_onboarding("b1"))
    msg = IncomingMessage(
        business_id="b1", team_id=None, text="We're Bright", message_id="m1", sent_at=NOW
    )
    await collect(brain.handle_message(msg))
    assert {call.reasoning for call in llm.calls} == {False}
    assert any(call.schema is Extraction for call in llm.calls)

@pytest.mark.parametrize("jev_says, expected", [(0.9, True), (0.1, False)])
async def test_planning_reasons_and_jev_decides_the_rest(
    brain, collect, llm, jev, script, team, message, jev_says, expected
):
    script()
    jev.answers["needs_reasoning"] = jev_says
    await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    seen = reasoning_by_kind(llm)
    assert seen.pop("LeadPlan") == {True}
    assert seen and all(flags == {expected} for flags in seen.values())

async def test_chat_reply_follows_jev(brain, collect, llm, jev, script, team, message):
    script(work=0.1)
    jev.answers["needs_reasoning"] = 0.9
    await collect(brain.handle_message(message(team, "Thanks!")))
    assert [call.reasoning for call in llm.calls] == [True]

async def test_learning_from_an_edit_asks_jev(brain, collect, llm, jev, script, team, message):
    script()
    llm.structured_responses["ReflectOutput"] = {"lessons": []}
    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    (draft,) = [e for e in events if isinstance(e, NeedsApproval)]
    jev.answers["needs_reasoning"] = 0.9
    jev.calls.clear()
    decision = ApprovalDecision(
        business_id="b1", approval_id=draft.approval_id, decision="edit", edited_text="Friday!"
    )
    await collect(brain.resolve_approval(decision))
    assert any("needs_reasoning" in questions for _, _, questions in jev.calls)
    (reflect,) = [call for call in llm.calls if call.schema is ReflectOutput]
    assert reflect.reasoning is True
