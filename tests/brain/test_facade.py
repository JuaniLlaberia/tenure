from brain.brain import TenureBrain, create_brain
from brain.deps import Settings
from brain.fakes import FakeJev, FakeLLM, FakeTools, InMemoryStore
from brain.helpers.jev import OpenRouterJev
from brain.helpers.llm import OpenRouterLLM
from brain.usage import MeteredJev, MeteredLLM
from contract import (
    ActionDone,
    ApprovalDecision,
    Ask,
    Error,
    NeedsApproval,
    TemplateInfo,
)

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def test_list_templates(brain):
    templates = brain.list_templates()
    assert [t.name for t in templates] == ["design", "marketing"]
    assert isinstance(templates[1], TemplateInfo)
    assert templates[1].personas[0].name == "Maya"

async def test_unknown_team_yields_error(brain, collect, make_team, message):
    team = await make_team()
    stranger = team.model_copy(update={"team_id": "no-such-team"})

    events = await collect(brain.handle_message(message(stranger, "Hello")))

    assert [type(e) for e in events] == [Error]
    assert events[0].recoverable

async def test_message_from_other_business_yields_error(brain, collect, make_team, message):
    team = await make_team(business_id="b1")
    intruder = team.model_copy(update={"business_id": "b2"})

    events = await collect(brain.handle_message(message(intruder, "Post something")))

    assert [type(e) for e in events] == [Error]

async def test_graph_exception_yields_error_never_raises(
    brain, collect, deps, script, make_team, message
):
    team = await make_team()
    script(work=0.1)

    async def boom(*args, **kwargs):
        raise RuntimeError("the model provider is down")

    deps.llm.complete = boom

    events = await collect(brain.handle_message(message(team, "Thanks!")))

    assert [type(e) for e in events] == [Error]
    assert events[0].recoverable
    assert events[0].team_id == team.team_id

async def test_approve_through_facade_posts(
    brain, collect, deps, tools, script, make_team, message
):
    team = await make_team()
    script()
    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    needs = of(events, NeedsApproval)[0]

    done = await collect(
        brain.resolve_approval(
            ApprovalDecision(business_id="b1", approval_id=needs.approval_id, decision="approve")
        )
    )

    assert of(done, ActionDone)
    assert ("post_social", {"business_id": "b1", "text": "We launch Friday!"}) in tools.calls

async def test_reject_with_reason_revises_into_new_approval(
    brain, collect, deps, llm, script, make_team, message
):
    team = await make_team()
    script()
    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    first = of(events, NeedsApproval)[0]

    revised = await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1",
                approval_id=first.approval_id,
                decision="reject",
                reason="Too salesy",
            )
        )
    )

    second = of(revised, NeedsApproval)
    assert len(second) == 1
    assert second[0].approval_id != first.approval_id
    assert second[0].task_id == first.task_id
    assert (await deps.store.get_task(first.task_id)).revisions == 1
    assert (await deps.store.get_approval(first.approval_id)).status == "rejected"
    writer_calls = [
        c for c in llm.calls if c.kind == "complete" and "You are Leo" in str(c.messages)
    ]
    assert "Too salesy" in str(writer_calls[-1].messages)

async def test_threads_are_separate_per_team(brain, collect, jev, script, make_team, message):
    first_team = await make_team()
    second_team = await make_team()
    script(clear=0.2, question="Which day is the launch?")

    paused = await collect(brain.handle_message(message(first_team, "Post about the launch")))
    assert isinstance(paused[-1], Ask)

    jev.answers["is_clear"] = 0.9
    other = await collect(brain.handle_message(message(second_team, "Post about Friday")))
    assert of(other, NeedsApproval)
    assert not of(other, Ask)

    resumed = await collect(brain.handle_message(message(first_team, "Friday", "m2")))
    assert of(resumed, NeedsApproval)

async def test_create_brain_builds_from_env():
    store, tools = InMemoryStore(), FakeTools()

    brain = create_brain(store, tools, settings=Settings(), llm=FakeLLM(), jev=FakeJev())
    assert isinstance(brain, TenureBrain)
    assert brain.deps.store is store
    assert [t.name for t in brain.list_templates()] == ["design", "marketing"]

    real = create_brain(store, tools, settings=Settings())
    assert isinstance(real.deps.llm, MeteredLLM) and isinstance(real.deps.llm._llm, OpenRouterLLM)
    assert isinstance(real.deps.jev, MeteredJev) and isinstance(real.deps.jev._jev, OpenRouterJev)
