from brain.flows.hire import hire_team
from contract import AutonomyLevel, Error, TeamHired

async def test_hire_saves_team_and_trust_and_yields_team_hired(deps, collect):
    events = await collect(hire_team(deps, "b1", "marketing"))

    assert len(events) == 1
    hired = events[0]
    assert isinstance(hired, TeamHired)
    assert hired.template == "marketing"
    assert hired.display_name == "Marketing"
    assert [p.name for p in hired.personas] == ["Maya", "Leo", "Sam"]

    team = await deps.store.get_team(hired.team_id)
    assert team.business_id == "b1"
    assert team.template == "marketing"
    assert not team.onboarded
    assert team.created_at == deps.clock()

    for task_type, level in [
        ("social_post", AutonomyLevel.ACT_AFTER_APPROVAL),
        ("newsletter", AutonomyLevel.ACT_AFTER_APPROVAL),
        ("competitor_check", AutonomyLevel.ACT_AND_REPORT),
    ]:
        trust = await deps.store.get_trust(hired.team_id, task_type)
        assert trust.level == level
        assert trust.approval_streak == 0

async def test_hire_unknown_template_yields_error(deps, collect):
    events = await collect(hire_team(deps, "b1", "astronauts"))

    assert len(events) == 1
    assert isinstance(events[0], Error)
    assert events[0].recoverable
    assert await deps.store.list_teams("b1") == []
