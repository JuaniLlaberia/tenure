from collections.abc import AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brain.common import guarded
from brain.deps import Deps, Settings
from brain.flows.approvals import resolve_approval
from brain.flows.hire import hire_team
from brain.flows.promotion import respond_promotion
from brain.flows.undo import undo_action
from brain.graphs.team import build_team_graph, new_request
from brain.helpers.jev import Jev, OpenRouterJev
from brain.helpers.llm import LLM, OpenRouterLLM
from brain.templates.loader import load_templates, template_info
from contract import (
    Approval,
    ApprovalDecision,
    Ask,
    Error,
    Event,
    IncomingMessage,
    PromotionResponse,
    Store,
    Team,
    TeamHired,
    TemplateInfo,
    Tools,
)

RECURSION_LIMIT = 200

class TenureBrain:
    """
    The Brain the app calls. Picks the thread, starts or resumes the right graph, turns its
    stream into contract Events, and never raises out of a stream.
    """

    def __init__(self, deps: Deps, checkpointer=None):
        self.deps = deps
        self.checkpointer = checkpointer or InMemorySaver()
        self.team_graph = build_team_graph(deps, self.checkpointer)

    def list_templates(self) -> list[TemplateInfo]:
        return [template_info(template) for template in self.deps.templates.values()]

    async def start_onboarding(self, business_id: str) -> AsyncIterator[Event]:
        yield Error(team_id=None, message="Business onboarding isn't ready yet.", recoverable=True)

    async def handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        async for event in guarded(self._handle(msg), team_id=msg.team_id):
            yield event

    async def hire_team(self, business_id: str, template: str) -> AsyncIterator[Event]:
        async for event in guarded(self._hire(business_id, template)):
            yield event

    async def resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]:
        async for event in resolve_approval(self.deps, decision, revise=self._revise):
            yield event

    async def respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]:
        async for event in respond_promotion(self.deps, response):
            yield event

    async def undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]:
        async for event in undo_action(self.deps, business_id, action_id):
            yield event

    async def _handle(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        if msg.team_id is None:
            yield Error(
                team_id=None,
                message="I can't take company-wide requests yet. Write in a team's topic.",
                recoverable=True,
            )
            return
        team = await self.deps.store.get_team(msg.team_id)
        if team is None or team.business_id != msg.business_id:
            yield Error(team_id=msg.team_id, message="I couldn't find that team.", recoverable=True)
            return
        snapshot = await self.team_graph.aget_state(self._config(team))
        if snapshot.interrupts:
            payload = Command(resume=msg.text)
        else:
            payload = new_request(team, msg.text, msg.message_id)
        async for event in self._run(team, payload):
            yield event

    async def _hire(self, business_id: str, template: str) -> AsyncIterator[Event]:
        hired: str | None = None
        async for event in hire_team(self.deps, business_id, template):
            yield event
            if isinstance(event, TeamHired):
                hired = event.team_id
        if hired is None:
            return
        team = await self.deps.store.get_team(hired)
        async for event in self._run(team, new_request(team, "", None)):
            yield event

    async def _revise(self, approval: Approval, reason: str) -> AsyncIterator[Event]:
        team = await self.deps.store.get_team(approval.team_id)
        payload = {
            **new_request(team, "", None),
            "revise": approval.approval_id,
            "feedback": reason,
        }
        async for event in self._run(team, payload):
            yield event

    def _config(self, team: Team) -> dict:
        return {
            "configurable": {"thread_id": f"{team.business_id}:{team.team_id}"},
            "recursion_limit": RECURSION_LIMIT,
        }

    async def _run(self, team: Team, payload) -> AsyncIterator[Event]:
        stream = self.team_graph.astream(
            payload, self._config(team), stream_mode=["custom", "updates"]
        )
        async for mode, chunk in stream:
            if mode == "custom":
                yield chunk
            elif "__interrupt__" in chunk:
                for pending in chunk["__interrupt__"]:
                    yield Ask.model_validate(pending.value)

def create_brain(
    store: Store,
    tools: Tools,
    settings: Settings | None = None,
    checkpointer=None,
    llm: LLM | None = None,
    jev: Jev | None = None,
) -> TenureBrain:
    """
    What the app calls: real OpenRouter clients by default, settings from the environment.
    """
    settings = settings or Settings.from_env()
    deps = Deps(
        store=store,
        tools=tools,
        llm=llm or OpenRouterLLM(settings),
        jev=jev or OpenRouterJev(settings),
        settings=settings,
        templates=load_templates(),
    )
    return TenureBrain(deps, checkpointer)
