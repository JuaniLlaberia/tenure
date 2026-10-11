import pytest

from brain.learning import ReflectOutput
from contract import ApprovalDecision, LessonLearned, NeedsApproval, Say

NO_HASHTAGS = {
    "lessons": [
        {
            "text": "No hashtags in posts",
            "kind": "preference",
            "business_wide": False,
            "task_type": "social_post",
            "replaces": None,
        }
    ]
}

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

def reflect_prompts(llm):
    return [
        "\n".join(str(m.get("content", "")) for m in call.messages)
        for call in llm.calls
        if call.schema is ReflectOutput
    ]

def writer_prompts(llm):
    return [
        str(call.messages)
        for call in llm.calls
        if call.kind == "complete" and "You are Leo" in str(call.messages)
    ]

@pytest.fixture
async def team(make_team):
    return await make_team()

async def first_draft(brain, collect, message, team, text="Post about our Friday launch"):
    events = await collect(brain.handle_message(message(team, text)))
    return of(events, NeedsApproval)[0]

async def test_chat_feedback_becomes_lesson(brain, collect, deps, llm, script, team, message):
    script(task_types=(), feedback=0.95)
    llm.structured_responses["ReflectOutput"] = NO_HASHTAGS

    events = await collect(brain.handle_message(message(team, "Stop using hashtags", "m7")))

    assert [type(e) for e in events] == [LessonLearned, Say]
    lesson = deps.store.lessons[events[0].lesson_id]
    assert (lesson.source, lesson.source_ref) == ("chat", "m7")
    assert deps.store.tasks == {}

async def test_one_off_instructions_are_not_learned(
    brain, collect, deps, jev, llm, script, team, message
):
    script(feedback=0.9)
    jev.answers["one_off"] = 0.9

    events = await collect(
        brain.handle_message(message(team, "Post about Friday, mention the 20% launch discount"))
    )

    assert not of(events, LessonLearned)
    assert not [call for call in llm.calls if call.schema is ReflectOutput]
    assert of(events, NeedsApproval)

async def test_feedback_and_work_in_one_message(brain, collect, llm, script, team, message):
    script(feedback=0.95)
    llm.structured_responses["ReflectOutput"] = NO_HASHTAGS

    events = await collect(
        brain.handle_message(message(team, "Stop using hashtags. Also post about Friday."))
    )

    learned = of(events, LessonLearned)
    needs = of(events, NeedsApproval)
    assert learned and needs
    assert events.index(learned[0]) < events.index(needs[0])
    assert "No hashtags in posts" in writer_prompts(llm)[0]

async def test_no_feedback_no_reflect_call(brain, collect, llm, script, team, message):
    script(feedback=0.1)

    await collect(brain.handle_message(message(team, "Post about our Friday launch")))

    assert reflect_prompts(llm) == []

async def test_edit_becomes_lesson(brain, collect, deps, llm, script, team, message):
    script()
    llm.structured_responses["ReflectOutput"] = NO_HASHTAGS
    needs = await first_draft(brain, collect, message, team)

    events = await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1",
                approval_id=needs.approval_id,
                decision="edit",
                edited_text="Friday. Be there.",
            )
        )
    )

    learned = of(events, LessonLearned)
    assert learned
    lesson = deps.store.lessons[learned[0].lesson_id]
    assert (lesson.source, lesson.source_ref) == ("edit", needs.approval_id)
    prompt = reflect_prompts(llm)[-1]
    assert "We launch Friday!" in prompt
    assert "Friday. Be there." in prompt

async def test_reject_with_reason_learns_then_revises(
    brain, collect, deps, llm, script, team, message
):
    script()
    llm.structured_responses["ReflectOutput"] = {
        "lessons": [{**NO_HASHTAGS["lessons"][0], "text": "Keep posts low-key, no hype"}]
    }
    needs = await first_draft(brain, collect, message, team)

    events = await collect(
        brain.resolve_approval(
            ApprovalDecision(
                business_id="b1",
                approval_id=needs.approval_id,
                decision="reject",
                reason="Too salesy",
            )
        )
    )

    learned = of(events, LessonLearned)
    revised = of(events, NeedsApproval)
    assert learned and revised
    assert events.index(learned[0]) < events.index(revised[0])
    assert revised[0].approval_id != needs.approval_id
    lesson = deps.store.lessons[learned[0].lesson_id]
    assert (lesson.source, lesson.source_ref) == ("reject", needs.approval_id)
    last_writer = writer_prompts(llm)[-1]
    assert "Keep posts low-key, no hype" in last_writer
    assert "Too salesy" in last_writer

async def test_lessons_reach_the_check(brain, collect, jev, llm, script, team, message):
    script(feedback=0.95)
    llm.structured_responses["ReflectOutput"] = NO_HASHTAGS

    await collect(
        brain.handle_message(message(team, "Stop using hashtags. Also post about Friday."))
    )

    check_states = [state for _, state, questions in jev.calls if "passes_check" in questions]
    assert "No hashtags in posts" in check_states[0]

async def test_approved_drafts_become_examples(brain, collect, llm, script, team, message):
    script()
    needs = await first_draft(brain, collect, message, team)
    await collect(
        brain.resolve_approval(
            ApprovalDecision(business_id="b1", approval_id=needs.approval_id, decision="approve")
        )
    )
    llm.structured_responses["SocialPostOutput"] = {"text": "Second post"}

    await collect(brain.handle_message(message(team, "Post about our open studio", "m2")))

    second_writer = writer_prompts(llm)[-1]
    assert "Drafts the founder approved" in second_writer
    assert "We launch Friday!" in second_writer
