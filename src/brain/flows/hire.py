from collections.abc import AsyncIterator

from brain.common import guarded, new_id
from brain.deps import Deps
from brain.flows.autonomy import initial_trust
from contract import Error, Event, Team, TeamHired

async def hire_team(deps: Deps, business_id: str, template: str) -> AsyncIterator[Event]:
    async for event in guarded(_hire(deps, business_id, template)):
        yield event

async def _hire(deps: Deps, business_id: str, template: str) -> AsyncIterator[Event]:
    spec = deps.templates.get(template)
    if spec is None:
        message = f"I don't know a team called {template!r}."
        yield Error(team_id=None, message=message, recoverable=True)
        return
    now = deps.clock()
    team = Team(
        team_id=new_id(),
        business_id=business_id,
        template=spec.name,
        display_name=spec.display_name,
        onboarded=False,
        created_at=now,
    )
    await deps.store.save_team(team)
    for trust in initial_trust(team.team_id, spec, now):
        await deps.store.set_trust(trust)
    yield TeamHired(
        team_id=team.team_id,
        template=spec.name,
        display_name=spec.display_name,
        personas=spec.personas(),
    )
