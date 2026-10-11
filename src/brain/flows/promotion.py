from collections.abc import AsyncIterator

from brain.common import guarded
from brain.deps import Deps
from brain.flows.approvals import label
from brain.flows.autonomy import is_above, next_level, reset_streak
from contract import AutonomyLevel, Error, Event, PromotionResponse, Say

ACCEPTED = {
    AutonomyLevel.ACT_AFTER_APPROVAL: "Thanks! I'll act on {label}s as soon as you approve them.",
    AutonomyLevel.ACT_AND_REPORT: (
        "Thanks! From now on I'll handle {label}s myself and tell you right after."
    ),
    AutonomyLevel.AUTONOMOUS: "Thanks! I'll handle {label}s on my own from now on.",
}

async def respond_promotion(deps: Deps, response: PromotionResponse) -> AsyncIterator[Event]:
    async for event in guarded(_respond(deps, response), team_id=response.team_id):
        yield event

async def _respond(deps: Deps, response: PromotionResponse) -> AsyncIterator[Event]:
    team = await deps.store.get_team(response.team_id)
    trust = await deps.store.get_trust(response.team_id, response.task_type)
    if (
        team is None
        or trust is None
        or team.business_id != response.business_id
        or team.template not in deps.templates
    ):
        yield Error(
            team_id=response.team_id,
            message="I couldn't find that team's settings.",
            recoverable=True,
        )
        return
    template = deps.templates[team.template]
    lead = template.lead.persona
    now = deps.clock()
    name = label(response.task_type)

    if not response.accepted:
        await deps.store.set_trust(reset_streak(trust, now))
        text = "No problem, nothing changes. I'll keep checking with you first."
        yield Say(team_id=team.team_id, persona=lead, text=text)
        return

    proposed = next_level(trust.level)
    if proposed is None or is_above(proposed, template.max_level(response.task_type)):
        await deps.store.set_trust(reset_streak(trust, now))
        text = f"I already have as much freedom as I can on {name}s."
        yield Say(team_id=team.team_id, persona=lead, text=text)
        return
    promoted = trust.model_copy(update={"level": proposed, "approval_streak": 0, "updated_at": now})
    await deps.store.set_trust(promoted)
    yield Say(team_id=team.team_id, persona=lead, text=ACCEPTED[proposed].format(label=name))
