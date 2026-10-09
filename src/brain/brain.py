from collections.abc import AsyncIterator
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brain.common import guarded, utcnow
from brain.deps import Deps, Settings
from brain.flows.approvals import resolve_approval
from brain.flows.hire import hire_team
from brain.flows.promotion import respond_promotion
from brain.flows.undo import undo_action
from brain.graphs.company import CHIEF_OF_STAFF, build_company_graph
from brain.graphs.team import (
    TeamState,
    build_team_graph,
    cross_step,
    home_task_type,
    new_request,
)
from brain.helpers.decide import decide
from brain.helpers.images import ImageClient, OpenRouterImages
from brain.helpers.jev import Jev, OpenRouterJev
from brain.helpers.llm import LLM, OpenRouterLLM
from brain.learning import learn
from brain.media import (
    VIDEO_REPLY,
    MediaText,
    attachments_text,
    describe,
    onboarding_answer,
    photos,
    status,
)
from brain.prompts.lead import ABOUT_IMAGE
from brain.templates.loader import load_templates, template_info
from brain.usage import MeteredImages, MeteredJev, MeteredLLM, UsageLog, enter
from contract import (
    Approval,
    ApprovalDecision,
    Ask,
    Error,
    Event,
    IncomingMessage,
    Persona,
    Progress,
    PromotionResponse,
    Say,
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
        self.team_graph = build_team_graph(deps, self.checkpointer, learn=self._learn_from_chat)
        self.company_graph = build_company_graph(deps, self.checkpointer)
        self._image_feedback: dict[str, bool] = {}

    def list_templates(self) -> list[TemplateInfo]:
        return [template_info(template) for template in self.deps.templates.values()]

    async def start_onboarding(self, business_id: str) -> AsyncIterator[Event]:
        enter(business_id)
        async for event in guarded(self._start(business_id)):
            yield event

    async def handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        enter(msg.business_id, msg.team_id)
        async for event in guarded(self._handle(msg), team_id=msg.team_id):
            yield event

    async def hire_team(self, business_id: str, template: str) -> AsyncIterator[Event]:
        enter(business_id)
        async for event in guarded(self._hire(business_id, template)):
            yield event

    async def resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]:
        enter(decision.business_id)
        stream = resolve_approval(
            self.deps, decision, learn=self._learn_from_approval, revise=self._revise
        )
        async for event in stream:
            yield event

    async def respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]:
        enter(response.business_id, response.team_id)
        async for event in respond_promotion(self.deps, response):
            yield event

    async def undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]:
        enter(business_id)
        async for event in undo_action(self.deps, business_id, action_id):
            yield event

    async def run_schedule(self, business_id: str, schedule_id: str) -> AsyncIterator[Event]:
        enter(business_id)
        async for event in guarded(self._run_schedule(business_id, schedule_id)):
            yield event

    async def _run_schedule(self, business_id: str, schedule_id: str) -> AsyncIterator[Event]:
        """
        Runs a schedule's request as a new request in its team, tagged with the schedule. The
        graph never asks during it: the founder isn't there.
        """
        schedule = await self.deps.store.get_schedule(schedule_id)
        team = await self.deps.store.get_team(schedule.team_id) if schedule else None
        if (
            schedule is None
            or not schedule.active
            or schedule.business_id != business_id
            or team is None
        ):
            message = "That schedule is stopped or doesn't exist."
            yield Error(team_id=None, message=message, recoverable=False)
            return
        enter(business_id, team.team_id)
        snapshot = await self.team_graph.aget_state(self._config(team))
        if snapshot.interrupts:
            message = "Waiting for your answer first"
            yield Error(team_id=team.team_id, message=message, recoverable=True)
            return
        payload = {
            **new_request(team, schedule.request, None),
            "schedule_id": schedule.schedule_id,
        }
        async for event in self._run(team, payload):
            yield event

    async def _handle(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        if msg.team_id is None:
            async for event in self._company(msg):
                yield event
            return
        team = await self.deps.store.get_team(msg.team_id)
        if team is None or team.business_id != msg.business_id:
            yield Error(team_id=msg.team_id, message="I couldn't find that team.", recoverable=True)
            return
        lead = self.deps.templates[team.template].lead.persona
        described: list[MediaText] = []
        async for event in self._read(msg, lead, described):
            yield event
        text = attachments_text(msg.text, described)
        if msg.attachments and not text:
            return
        snapshot = await self.team_graph.aget_state(self._config(team))
        if snapshot.interrupts:
            payload = Command(resume=text)
        else:
            payload = new_request(team, text, msg.message_id, photos(described))
        async for event in self._run(team, payload):
            yield event

    async def _read(
        self, msg: IncomingMessage, persona: Persona, found: list[MediaText]
    ) -> AsyncIterator[Event]:
        """
        Turns each attachment into text before anything decides (Jev only reads text), with a
        status line per file. Videos get one polite reply instead.
        """
        videos = False
        for file in msg.attachments:
            line = status(persona, file)
            if line:
                yield Progress(team_id=msg.team_id, persona=persona, status=line)
            described = await describe(self.deps, msg.business_id, file)
            if described is None:
                videos = True
            else:
                found.append(described)
        if videos:
            yield Say(team_id=msg.team_id, persona=persona, text=VIDEO_REPLY)

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

    async def _learn_from_chat(self, state: TeamState, message: str) -> AsyncIterator[Event]:
        stream = learn(
            self.deps,
            business_id=state["business_id"],
            team_id=state["team_id"],
            template=self.deps.templates[state["template"]],
            source="chat",
            source_ref=state.get("message_id"),
            feedback=message,
            reasoning=state.get("reasoning", False),
        )
        async for event in stream:
            yield event

    async def _learn_from_approval(
        self, approval: Approval, source: Literal["edit", "reject"], feedback: str
    ) -> AsyncIterator[Event]:
        enter(approval.business_id, approval.team_id)
        team = await self.deps.store.get_team(approval.team_id)
        if source == "reject":
            image_team = await self._image_team(approval)
            if image_team is not None:
                other, task_type = image_team
                async for event in self._learn_for(other, task_type, team, approval, feedback):
                    yield event
                return
        stream = learn(
            self.deps,
            business_id=approval.business_id,
            team_id=approval.team_id,
            template=self.deps.templates[team.template],
            source=source,
            source_ref=approval.approval_id,
            feedback=feedback,
            task_type=approval.task_type,
        )
        async for event in stream:
            yield event

    async def _image_team(self, approval: Approval) -> tuple[Team, str | None] | None:
        """
        The other team whose step made this draft's image, and that step's task type there,
        when the founder's reason is only about the image (one decide() per rejection,
        remembered for the revision).
        """
        task = await self.deps.store.get_task(approval.task_id)
        cross = cross_step(task.steps) if task else None
        if cross is None:
            return None
        if approval.approval_id not in self._image_feedback:
            state = f"Feedback on the draft:\n{approval.reason}"
            decisions = await decide(self.deps, {"about_image": ABOUT_IMAGE}, state)
            accepted = decisions["about_image"].accepts("yes", self.deps.settings.decide_threshold)
            self._image_feedback[approval.approval_id] = accepted
        if not self._image_feedback[approval.approval_id]:
            return None
        name, _, specialist_id = task.steps[cross].partition(":")
        teams = await self.deps.store.list_teams(approval.business_id)
        other = next((t for t in teams if t.template == name and t.onboarded), None)
        if other is None or name not in self.deps.templates:
            return None
        return other, home_task_type(self.deps.templates[name], specialist_id)

    async def _learn_for(
        self,
        other: Team,
        task_type: str | None,
        team: Team,
        approval: Approval,
        feedback: str,
    ) -> AsyncIterator[Event]:
        """
        Image feedback teaches the team that made the image; the lead here says so.
        """
        template = self.deps.templates[other.template]
        noted = []
        stream = learn(
            self.deps,
            business_id=approval.business_id,
            team_id=other.team_id,
            template=template,
            source="reject",
            source_ref=approval.approval_id,
            feedback=feedback,
            task_type=task_type,
        )
        async for event in stream:
            noted.append(event.text)
            yield event
        if noted:
            lesson = noted[0][0].lower() + noted[0][1:]
            yield Say(
                team_id=team.team_id,
                task_id=approval.task_id,
                persona=self.deps.templates[team.template].lead.persona,
                text=f"{template.lead.persona.name} noted that for next time: {lesson}",
            )

    async def _revise(self, approval: Approval, reason: str) -> AsyncIterator[Event]:
        enter(approval.business_id, approval.team_id)
        team = await self.deps.store.get_team(approval.team_id)
        await self._image_team(approval)
        payload = {
            **new_request(team, "", None),
            "revise": approval.approval_id,
            "feedback": reason,
            "image_feedback": self._image_feedback.pop(approval.approval_id, None),
        }
        async for event in self._run(team, payload):
            yield event

    def _config(self, team: Team) -> dict:
        return _thread(f"{team.business_id}:{team.team_id}")

    async def _run(self, team: Team, payload) -> AsyncIterator[Event]:
        async for event in _stream(self.team_graph, self._config(team), payload):
            yield event

    async def _start(self, business_id: str) -> AsyncIterator[Event]:
        profile = await self.deps.store.get_profile(business_id)
        if profile is not None:
            yield Say(
                team_id=None,
                persona=CHIEF_OF_STAFF,
                text=(
                    f"We're already set up for {profile.name}. Ask me anything here, or hire a "
                    "team with /hire."
                ),
            )
            return
        payload = {"business_id": business_id, "restart": True, "message": None}
        async for event in _stream(self.company_graph, _thread(f"{business_id}:company"), payload):
            yield event

    async def _company(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        described: list[MediaText] = []
        async for event in self._read(msg, CHIEF_OF_STAFF, described):
            yield event
        text = attachments_text(msg.text, described)
        if msg.attachments and not text:
            return
        config = _thread(f"{msg.business_id}:company")
        snapshot = await self.company_graph.aget_state(config)
        if snapshot.interrupts:
            payload = Command(resume=onboarding_answer(msg.text, described))
        else:
            payload = {"business_id": msg.business_id, "restart": False, "message": text}
        async for event in _stream(self.company_graph, config, payload):
            yield event

def _thread(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}

async def _stream(graph, config: dict, payload) -> AsyncIterator[Event]:
    """
    Runs a graph and turns its stream into contract Events: custom chunks are Events already;
    an interrupt carries the Ask that ends the stream.
    """
    stream = graph.astream(payload, config, stream_mode=["custom", "updates"])
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
    images: ImageClient | None = None,
) -> TenureBrain:
    """
    What the app calls: real OpenRouter clients by default, settings from the environment.
    """
    settings = settings or Settings.from_env()
    usage = UsageLog(store, settings, utcnow)
    deps = Deps(
        store=store,
        tools=tools,
        llm=MeteredLLM(llm or OpenRouterLLM(settings), usage),
        jev=MeteredJev(jev or OpenRouterJev(settings), usage),
        settings=settings,
        templates=load_templates(),
        images=MeteredImages(images or OpenRouterImages(settings), usage),
    )
    return TenureBrain(deps, checkpointer)
