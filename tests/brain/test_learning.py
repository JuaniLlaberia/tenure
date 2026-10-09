from brain.common import new_id
from brain.learning import ReflectOutput, learn, reflect
from contract import Lesson, LessonLearned

NO_HASHTAGS = {
    "text": "No hashtags in posts",
    "kind": "preference",
    "business_wide": False,
    "task_type": "social_post",
    "replaces": None,
}

def existing_lesson(deps, team_id, text, lesson_id=None, task_type=None):
    return Lesson(
        lesson_id=lesson_id or new_id(),
        business_id="b1",
        team_id=team_id,
        task_type=task_type,
        kind="preference",
        text=text,
        source="chat",
        created_at=deps.clock(),
    )

def reflect_prompt(llm):
    call = next(c for c in llm.calls if c.schema is ReflectOutput)
    return "\n".join(str(m.get("content", "")) for m in call.messages)

async def run_learn(deps, collect, team, feedback="Stop using hashtags", **overrides):
    kwargs = {
        "business_id": team.business_id,
        "team_id": team.team_id,
        "template": deps.templates[team.template],
        "source": "chat",
        "source_ref": "m1",
        "feedback": feedback,
    }
    kwargs.update(overrides)
    return await collect(learn(deps, **kwargs))

async def test_reflect_returns_drafts(deps, llm, make_team):
    team = await make_team()
    old = existing_lesson(deps, team.team_id, "Sign posts as Juan", lesson_id="lesson-42")
    llm.structured_responses["ReflectOutput"] = {"lessons": [NO_HASHTAGS]}

    drafts, tokens = await reflect(
        deps, deps.templates["marketing"], "Stop using hashtags", [old]
    )

    assert [d.text for d in drafts] == ["No hashtags in posts"]
    assert tokens == llm.tokens_per_call
    prompt = reflect_prompt(llm)
    assert "Stop using hashtags" in prompt
    assert "lesson-42" in prompt
    assert "Sign posts as Juan" in prompt
    assert llm.calls[0].model == deps.settings.model_reflect

async def test_learn_saves_team_lesson_and_yields_event(deps, collect, llm, make_team):
    team = await make_team()
    llm.structured_responses["ReflectOutput"] = {"lessons": [NO_HASHTAGS]}

    events = await run_learn(deps, collect, team)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, LessonLearned)
    assert event.team_id == team.team_id
    assert event.persona.name == "Maya"
    assert event.text == "No hashtags in posts"
    assert not event.business_wide
    lesson = deps.store.lessons[event.lesson_id]
    assert lesson.team_id == team.team_id
    assert lesson.task_type == "social_post"
    assert lesson.kind == "preference"
    assert (lesson.source, lesson.source_ref) == ("chat", "m1")
    assert lesson.created_at == deps.clock()

async def test_learn_saves_business_wide_lesson(deps, collect, llm, make_team):
    team = await make_team()
    other = await make_team()
    llm.structured_responses["ReflectOutput"] = {
        "lessons": [
            {
                "text": "We never offer discounts",
                "kind": "fact",
                "business_wide": True,
                "task_type": None,
                "replaces": None,
            }
        ]
    }

    events = await run_learn(deps, collect, team, feedback="We never do discounts, ever")

    assert events[0].business_wide
    assert events[0].team_id == team.team_id
    lesson = deps.store.lessons[events[0].lesson_id]
    assert lesson.team_id is None
    other_sees = await deps.store.list_lessons("b1", other.team_id)
    assert [item.text for item in other_sees] == ["We never offer discounts"]

async def test_learn_replaces_existing_lesson(deps, collect, llm, make_team):
    team = await make_team()
    old = existing_lesson(deps, team.team_id, "Use a few hashtags", lesson_id="old-1")
    await deps.store.save_lesson(old)
    llm.structured_responses["ReflectOutput"] = {
        "lessons": [{**NO_HASHTAGS, "replaces": "old-1"}]
    }

    await run_learn(deps, collect, team)

    assert not deps.store.lessons["old-1"].active
    active = await deps.store.list_lessons("b1", team.team_id)
    assert [item.text for item in active] == ["No hashtags in posts"]

async def test_learn_ignores_unknown_replaces_and_task_type(deps, collect, llm, make_team):
    team = await make_team()
    keep = existing_lesson(deps, team.team_id, "Sign posts as Juan", lesson_id="keep-1")
    await deps.store.save_lesson(keep)
    llm.structured_responses["ReflectOutput"] = {
        "lessons": [{**NO_HASHTAGS, "replaces": "no-such-lesson", "task_type": "podcast"}]
    }

    events = await run_learn(deps, collect, team)

    assert deps.store.lessons["keep-1"].active
    assert deps.store.lessons[events[0].lesson_id].task_type is None

async def test_learn_keeps_at_most_two(deps, collect, llm, make_team):
    team = await make_team()
    llm.structured_responses["ReflectOutput"] = {
        "lessons": [{**NO_HASHTAGS, "text": f"Lesson {n}"} for n in range(4)]
    }

    events = await run_learn(deps, collect, team)

    assert [e.text for e in events] == ["Lesson 0", "Lesson 1"]

async def test_learn_drops_empty_and_duplicate_lessons(deps, collect, llm, make_team):
    team = await make_team()
    await deps.store.save_lesson(existing_lesson(deps, team.team_id, "No hashtags in posts"))
    llm.structured_responses["ReflectOutput"] = {
        "lessons": [
            {**NO_HASHTAGS, "text": "   "},
            {**NO_HASHTAGS, "text": "no hashtags in posts"},
        ]
    }

    events = await run_learn(deps, collect, team)

    assert events == []
    assert len(await deps.store.list_lessons("b1", team.team_id)) == 1

async def test_learn_survives_reflect_failure(deps, collect, llm, make_team):
    team = await make_team()
    llm.fail = True

    events = await run_learn(deps, collect, team)

    assert events == []
    assert deps.store.lessons == {}

async def test_learn_passes_task_type_hint(deps, collect, llm, make_team):
    team = await make_team()
    llm.structured_responses["ReflectOutput"] = {"lessons": []}

    await run_learn(deps, collect, team, source="edit", source_ref="a1", task_type="newsletter")

    assert "newsletter" in reflect_prompt(llm)
