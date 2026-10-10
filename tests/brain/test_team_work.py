import pytest

from brain.fakes import InMemoryStore
from contract import (
    ActionDone,
    ApprovalDecision,
    Ask,
    AutonomyLevel,
    BusinessProfile,
    Error,
    NeedsApproval,
    PostSocial,
    Progress,
    Say,
    SendEmail,
    TaskStatus,
)

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def prompts_for(llm, persona_name):
    """
    The text of every specialist completion call made as `persona_name`.
    """
    texts = []
    for call in llm.calls:
        if call.kind != "complete":
            continue
        text = "\n".join(str(m.get("content", "")) for m in call.messages)
        if f"You are {persona_name}" in text:
            texts.append(text)
    return texts

async def tasks_of(deps, events):
    ids = {e.task_id for e in events if getattr(e, "task_id", None)}
    return [await deps.store.get_task(task_id) for task_id in ids]

@pytest.fixture
async def team(make_team):
    return await make_team()

async def test_post_request_ends_in_needs_approval(brain, collect, deps, script, team, message):
    script()

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    approvals = of(events, NeedsApproval)
    assert len(approvals) == 1
    needs = approvals[0]
    assert needs.task_type == "social_post"
    assert needs.planned_action == PostSocial(text="We launch Friday!")
    assert needs.preview == "We launch Friday!"
    assert needs.check_confidence == pytest.approx(0.9)
    assert needs.persona.name == "Maya"
    assert isinstance(events[-1], Say)
    assert events.index(needs) < len(events) - 1

    task = await deps.store.get_task(needs.task_id)
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert task.title == "Launch post"
    assert task.steps == ["writer"]
    approval = await deps.store.get_approval(needs.approval_id)
    assert approval.status == "pending"
    assert approval.planned_action == needs.planned_action

async def test_triage_is_one_jev_call_with_team_context(
    brain, collect, deps, jev, script, team, message
):
    script()

    await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    _, state, questions = jev.calls[0]
    assert set(questions) == {
        "has_feedback",
        "needs_reasoning",
        "names_channels",
        "needs_social_post",
        "needs_newsletter",
        "needs_competitor_check",
        "wants_schedule",
    }
    assert "Post about our Friday launch" in state
    for spec in deps.templates["marketing"].task_types.values():
        assert spec.description in state

async def test_routing_uses_route_threshold(brain, collect, deps, jev, script, team, message):
    script()
    jev.answers["needs_social_post"] = 0.65
    deps.settings = deps.settings.model_copy(update={"route_threshold": 0.6})

    events = await collect(brain.handle_message(message(team, "Get the word out")))

    assert of(events, NeedsApproval)

async def test_check_sees_business_profile(brain, collect, deps, jev, script, team, message):
    await deps.store.save_profile(
        BusinessProfile(
            business_id="b1",
            name="Juan's Studio",
            what_you_sell="Brand design for cafés",
            customers="Café owners",
            tone="warm, plain-spoken",
        )
    )
    script()

    await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    check_states = [state for _, state, questions in jev.calls if "passes_check" in questions]
    assert "warm, plain-spoken" in check_states[0]

async def test_progress_events_carry_task_id(brain, collect, script, team, message):
    script()

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    needs = of(events, NeedsApproval)[0]
    writer_progress = [e for e in of(events, Progress) if e.persona.name == "Leo"]
    assert writer_progress
    assert all(e.task_id == needs.task_id for e in writer_progress)

async def test_two_task_types_give_two_approvals(brain, collect, script, team, message):
    script(task_types=("newsletter", "social_post"))

    events = await collect(brain.handle_message(message(team, "Get the word out for Friday")))

    approvals = of(events, NeedsApproval)
    assert [a.task_type for a in approvals] == ["newsletter", "social_post"]
    assert len({a.approval_id for a in approvals}) == 2
    assert len({a.task_id for a in approvals}) == 2
    assert isinstance(approvals[0].planned_action, SendEmail)

async def test_newsletter_runs_researcher_then_writer(brain, collect, llm, script, team, message):
    script(task_types=("newsletter",))

    await collect(brain.handle_message(message(team, "Send a launch newsletter")))

    researcher = prompts_for(llm, "Sam")
    writer = prompts_for(llm, "Leo")
    assert researcher and writer
    assert "Rivals post on Mondays" in writer[0]
    def first_call_as(name):
        return next(
            i
            for i, c in enumerate(llm.calls)
            if c.kind == "complete" and f"You are {name}" in str(c.messages)
        )

    assert first_call_as("Sam") < first_call_as("Leo")

async def test_plan_drops_task_types_route_did_not_pick(
    brain, collect, deps, script, team, message
):
    plan = {
        "tasks": [
            {"task_type": "social_post", "title": "Launch post", "brief": "Post it"},
            {"task_type": "competitor_check", "title": "Rivals", "brief": "Check rivals"},
        ]
    }
    script(task_types=("social_post",), plan=plan)

    events = await collect(brain.handle_message(message(team, "Post about Friday")))

    tasks = await tasks_of(deps, events)
    assert [t.task_type for t in tasks] == ["social_post"]

async def test_unclear_request_asks_then_resumes(
    brain, collect, jev, llm, script, team, message
):
    script(clear=0.2, question="Which day is the launch?")

    first = await collect(brain.handle_message(message(team, "Post about the launch")))

    assert isinstance(first[-1], Ask)
    assert first[-1].question == "Which day is the launch?"
    assert not of(first, NeedsApproval)

    second = await collect(brain.handle_message(message(team, "Friday", "m2")))

    assert of(second, NeedsApproval)
    assert not of(second, Ask)
    clarity_calls = [c for c in jev.calls if "is_clear" in c[2]]
    assert len(clarity_calls) == 1
    plan_calls = [c for c in llm.calls if c.schema is not None and c.schema.__name__ == "LeadPlan"]
    assert "Friday" in str(plan_calls[-1].messages)

async def test_a_plain_yes_keeps_the_planned_work(
    brain, collect, jev, script, team, message
):
    script(task_types=("social_post", "newsletter"), clear=0.2, question="Post and newsletter?")
    first = await collect(brain.handle_message(message(team, "Start the launch campaign")))
    assert first[-1].question == "Post and newsletter?"

    jev.answers["needs_social_post"] = jev.answers["needs_newsletter"] = 0.1
    second = await collect(brain.handle_message(message(team, "yes", "m2")))

    assert {d.task_type for d in of(second, NeedsApproval)} == {"social_post", "newsletter"}
    assert not of(second, Ask)
    routing = [state for _, state, questions in jev.calls if "needs_newsletter" in questions]
    assert "Maya: Post and newsletter?\nFounder: yes" in routing[-1]

async def test_failed_check_reruns_writer_with_feedback(
    brain, collect, deps, jev, llm, script, team, message
):
    script()
    jev.answers["passes_check"] = [0.1, 0.9]

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    needs = of(events, NeedsApproval)[0]
    writer = prompts_for(llm, "Leo")
    assert len(writer) == 2
    assert "Mention the date" in writer[1]
    assert (await deps.store.get_task(needs.task_id)).revisions == 0

async def test_two_failed_checks_go_to_approval_anyway(
    brain, collect, deps, llm, script, team, message
):
    script(passes=0.1)

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    needs = of(events, NeedsApproval)[0]
    assert needs.check_confidence == pytest.approx(0.1)
    assert (await deps.store.get_task(needs.task_id)).revisions == 0
    assert len(prompts_for(llm, "Leo")) == 3

async def test_review_retries_leave_the_founder_both_revisions(
    brain, collect, deps, jev, llm, script, team, message
):
    script()
    jev.answers["passes_check"] = [0.1, 0.1, 0.1]

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    needs = of(events, NeedsApproval)[0]
    jev.answers["passes_check"] = 0.9
    for round_ in (1, 2):
        decision = ApprovalDecision(
            business_id=team.business_id,
            approval_id=needs.approval_id,
            decision="reject",
            reason="Mention the venue",
        )
        events = await collect(brain.resolve_approval(decision))
        needs = of(events, NeedsApproval)[0]
        assert (await deps.store.get_task(needs.task_id)).revisions == round_

    decision = ApprovalDecision(
        business_id=team.business_id,
        approval_id=needs.approval_id,
        decision="reject",
        reason="Shorter",
    )
    events = await collect(brain.resolve_approval(decision))
    assert not of(events, NeedsApproval)
    assert "dropped" in of(events, Say)[-1].text

async def test_each_founder_revision_gets_its_own_review_retries(
    brain, collect, jev, llm, script, team, message
):
    script()
    jev.answers["passes_check"] = [0.1, 0.1, 0.1]
    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    needs = of(events, NeedsApproval)[0]
    before = len(prompts_for(llm, "Leo"))
    jev.answers["passes_check"] = [0.1, 0.1, 0.1]

    decision = ApprovalDecision(
        business_id=team.business_id,
        approval_id=needs.approval_id,
        decision="reject",
        reason="Mention the venue",
    )
    events = await collect(brain.resolve_approval(decision))

    assert of(events, NeedsApproval)
    assert len(prompts_for(llm, "Leo")) - before == 3

async def test_act_and_report_acts_without_approval(
    brain, collect, deps, script, make_team, message
):
    team = await make_team(levels={"social_post": AutonomyLevel.ACT_AND_REPORT})
    script()

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    assert not of(events, NeedsApproval)
    done = of(events, ActionDone)
    assert len(done) == 1
    assert not done[0].autonomous
    entry = await deps.store.get_action(done[0].action_id)
    assert not entry.autonomous
    assert entry.approval_id is None
    assert (await deps.store.get_task(done[0].task_id)).status == TaskStatus.DONE

async def test_autonomous_marks_action_autonomous(
    brain, collect, deps, script, make_team, message
):
    team = await make_team(levels={"social_post": AutonomyLevel.AUTONOMOUS})
    script()

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    done = of(events, ActionDone)[0]
    assert done.autonomous
    assert (await deps.store.get_action(done.action_id)).autonomous

async def test_task_type_without_action_posts_result_as_say(
    brain, collect, deps, tools, script, team, message
):
    script(task_types=("competitor_check",))

    events = await collect(brain.handle_message(message(team, "What are rivals doing?")))

    assert not of(events, NeedsApproval)
    assert not of(events, ActionDone)
    report = [e for e in of(events, Say) if "Rivals cut prices this week" in e.text]
    assert report
    assert (await deps.store.get_task(report[0].task_id)).status == TaskStatus.DONE
    assert all(name not in ("post_social", "send_email") for name, _ in tools.calls)

async def test_draft_only_approval_has_no_planned_action(
    brain, collect, script, make_team, message
):
    team = await make_team(levels={"social_post": AutonomyLevel.DRAFT_ONLY})
    script()

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    needs = of(events, NeedsApproval)[0]
    assert needs.planned_action is None
    assert needs.preview == "We launch Friday!"

async def test_message_without_work_gets_one_say(brain, collect, deps, script, team, message):
    script(work=0.1)

    events = await collect(brain.handle_message(message(team, "Thanks, that was great")))

    assert [type(e) for e in events] == [Say]
    assert deps.store.tasks == {}

async def test_no_routed_task_type_gets_one_say(brain, collect, deps, script, team, message):
    script(task_types=())

    events = await collect(brain.handle_message(message(team, "Design me a logo")))

    assert [type(e) for e in events] == [Say]
    assert deps.store.tasks == {}

async def test_over_token_budget_fails_task_with_error(
    brain, collect, deps, script, team, message
):
    marketing = deps.templates["marketing"]
    deps.templates["marketing"] = marketing.model_copy(
        update={"limits": marketing.limits.model_copy(update={"token_budget": 1})}
    )
    script()

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    assert of(events, Error)
    assert of(events, Error)[0].recoverable
    assert not of(events, NeedsApproval)
    tasks = list(deps.store.tasks.values())
    assert [t.status for t in tasks] == [TaskStatus.FAILED]

async def test_specialist_exception_fails_task_not_stream(
    brain, collect, deps, llm, script, team, message
):
    script(task_types=("social_post", "newsletter"))
    del llm.structured_responses["SocialPostOutput"]

    events = await collect(brain.handle_message(message(team, "Get the word out")))

    assert of(events, Error)
    approvals = of(events, NeedsApproval)
    assert [a.task_type for a in approvals] == ["newsletter"]
    statuses = {t.task_type: t.status for t in deps.store.tasks.values()}
    assert statuses == {
        "social_post": TaskStatus.FAILED,
        "newsletter": TaskStatus.WAITING_APPROVAL,
    }

async def test_graph_store_failure_yields_error(brain, collect, deps, script, team, message):
    script()

    class BrokenStore(InMemoryStore):
        async def save_task(self, task):
            raise RuntimeError("database down")

    broken = BrokenStore()
    broken.teams = deps.store.teams
    broken.trust = deps.store.trust
    deps.store = broken

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    assert of(events, Error)
    assert events[-1].recoverable

async def test_specialists_get_their_craft_instructions(
    brain, collect, deps, llm, script, team, message
):
    script()
    await collect(brain.handle_message(message(team, "Post about our Friday launch")))
    writer = prompts_for(llm, "Leo")[0]
    assert deps.templates["marketing"].specialists["writer"].instructions[:40] in writer
    assert "How you work:" in writer

async def test_a_campaign_without_channels_asks_where_it_goes(
    brain, collect, jev, script, team, message
):
    script(task_types=("social_post", "newsletter"), named=0.1)
    first = await collect(brain.handle_message(message(team, "Start a campaign for our launch")))
    ask = first[-1]
    assert isinstance(ask, Ask) and ask.question == "Where should this go out?"
    assert ask.quick_replies == ["Bluesky and newsletter", "Only Bluesky", "Only the newsletter"]
    assert not of(first, NeedsApproval)

    jev.answers["needs_newsletter"] = 0.1
    second = await collect(brain.handle_message(message(team, "Only Bluesky", "m2")))

    assert [d.task_type for d in of(second, NeedsApproval)] == ["social_post"]

async def test_naming_a_channel_skips_the_question(brain, collect, script, team, message):
    script(named=0.9)
    events = await collect(brain.handle_message(message(team, "Make a Bluesky post")))
    assert of(events, NeedsApproval) and not of(events, Ask)

async def test_a_follow_up_sees_the_previous_request(
    brain, collect, llm, script, team, message
):
    script(task_types=("newsletter",))
    await collect(brain.handle_message(message(team, "Email about the Nov 15 app launch")))
    script(task_types=("social_post",))
    await collect(brain.handle_message(message(team, "Now make a Bluesky post", "m2")))

    plans = [c for c in llm.calls if c.schema is not None and c.schema.__name__ == "LeadPlan"]
    latest = str(plans[-1].messages)
    assert "Recent requests to this team" in latest
    assert "Email about the Nov 15 app launch" in latest

async def test_the_check_sees_what_the_founder_asked(
    brain, collect, jev, script, team, message
):
    script()
    await collect(brain.handle_message(message(team, "Announce our new dark mode on Bluesky")))
    checks = [state for _, state, questions in jev.calls if "passes_check" in questions]
    assert "What the founder asked for:\nAnnounce our new dark mode on Bluesky" in checks[0]

async def test_running_out_after_a_draft_still_sends_it_for_approval(
    brain, collect, deps, jev, script, team, message
):
    marketing = deps.templates["marketing"]
    deps.templates["marketing"] = marketing.model_copy(
        update={"limits": marketing.limits.model_copy(update={"token_budget": 120})}
    )
    script(passes=0.1)

    events = await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    (draft,) = of(events, NeedsApproval)
    assert draft.check_confidence == 0.0
    assert any("within budget" in e.text for e in of(events, Say))
    assert not of(events, Error)
