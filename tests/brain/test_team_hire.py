from contract import Ask, OnboardingComplete, Say, TeamHired

async def hire(brain, collect, template="marketing"):
    events = await collect(brain.hire_team("b1", template))
    return events, events[0].team_id

async def test_hire_yields_team_hired_then_first_onboarding_ask(brain, collect, deps):
    events, team_id = await hire(brain, collect)

    assert isinstance(events[0], TeamHired)
    assert events[0].display_name == "Marketing"
    last = events[-1]
    assert isinstance(last, Ask)
    assert last.team_id == team_id
    assert last.persona.name == "Maya"
    channels = deps.templates["marketing"].onboarding["channels"]
    assert last.question == channels.question
    assert last.quick_replies == channels.quick_replies == [
        "Bluesky and newsletter",
        "Only Bluesky",
        "Only the newsletter",
    ]
    assert all(e.team_id == team_id for e in events[1:])

async def test_onboarding_answers_become_team_facts(brain, collect, deps, message):
    _, team_id = await hire(brain, collect)
    team = await deps.store.get_team(team_id)
    questions = deps.templates["marketing"].onboarding

    first = await collect(brain.handle_message(message(team, "Bluesky and LinkedIn", "m1")))
    assert isinstance(first[-1], Ask)
    assert first[-1].question == questions["upcoming"].question
    second = await collect(brain.handle_message(message(team, "We launch Friday", "m2")))
    assert second[-1].question == questions["newsletter_to"].question
    third = await collect(brain.handle_message(message(team, "list@b.co", "m3")))

    assert any(isinstance(e, OnboardingComplete) and e.scope == "team" for e in third)
    assert isinstance(third[-1], Say)
    assert (await deps.store.get_team(team_id)).onboarded

    lessons = await deps.store.list_lessons("b1", team_id)
    by_key = {lesson.key: lesson for lesson in lessons}
    assert set(by_key) == {"channels", "upcoming", "newsletter_to"}
    assert all(lesson.kind == "fact" for lesson in lessons)
    assert all(lesson.source == "team_onboarding" for lesson in lessons)
    assert all(lesson.team_id == team_id for lesson in lessons)
    assert "Bluesky and LinkedIn" in by_key["channels"].text
    assert "list@b.co" in by_key["newsletter_to"].text

async def test_onboarding_asks_each_question_once(brain, collect, deps, message):
    _, team_id = await hire(brain, collect)
    team = await deps.store.get_team(team_id)

    asks = []
    for text in ["Bluesky", "Friday launch", "list@b.co"]:
        events = await collect(brain.handle_message(message(team, text)))
        asks.extend(e.question for e in events if isinstance(e, Ask))

    assert len(asks) == len(set(asks)) == 2

async def test_team_without_onboarding_questions_is_onboarded_at_once(brain, collect, deps):
    deps.templates["quiet"] = deps.templates["marketing"].model_copy(
        update={"name": "quiet", "onboarding": {}}
    )

    events, team_id = await hire(brain, collect, "quiet")

    assert not any(isinstance(e, Ask) for e in events)
    assert any(isinstance(e, OnboardingComplete) for e in events)
    assert (await deps.store.get_team(team_id)).onboarded
