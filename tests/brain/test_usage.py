import pytest

from brain.brain import TenureBrain
from brain.deps import Settings
from brain.helpers.llm import Completion, Usage
from brain.usage import MeteredJev, MeteredLLM, UsageLog, purposes
from contract import NeedsApproval

@pytest.fixture
def metered(deps, store, llm, jev, clock):
    usage = UsageLog(store, deps.settings, clock)
    deps.llm = MeteredLLM(llm, usage)
    deps.jev = MeteredJev(jev, usage)
    return TenureBrain(deps)

def test_purposes_name_what_each_model_does():
    found = purposes(Settings())
    assert found["anthropic/claude-sonnet-5.5"] == "Reviews"
    assert found["typesafe/jev-1.13"] == "Decisions"
    assert "Planning and chat" in found["deepseek/deepseek-v4-flash-0731"]

async def test_every_call_logs_usage_with_its_scope(
    metered, collect, store, llm, jev, script, make_team, message, deps
):
    team = await make_team()
    script()
    usage = Usage(input_tokens=7, output_tokens=3, cost=0.01)
    llm.completions = [Completion(text="Done.", usage=usage)]
    jev.input_tokens = 100

    events = await collect(metered.handle_message(message(team, "Post about our Friday launch")))

    (draft,) = [e for e in events if isinstance(e, NeedsApproval)]
    assert store.usage
    assert {(u.business_id, u.team_id) for u in store.usage} == {(team.business_id, team.team_id)}
    jev_rows = [u for u in store.usage if u.model == deps.settings.model_jev]
    assert jev_rows and all(u.purpose == "Decisions" and u.input_tokens == 100 for u in jev_rows)
    writer = [u for u in store.usage if u.cost == 0.01]
    assert writer and all(u.task_id == draft.task_id for u in writer)
    assert (writer[0].input_tokens, writer[0].output_tokens) == (7, 3)
    assert any(u.task_id is None for u in store.usage)

async def test_a_failed_usage_log_never_breaks_the_work(
    metered, collect, store, script, make_team, message
):
    async def broken(usage):
        raise RuntimeError("database is down")

    store.log_usage = broken
    team = await make_team()
    script()

    events = await collect(metered.handle_message(message(team, "Post about our Friday launch")))

    assert [e for e in events if isinstance(e, NeedsApproval)]
