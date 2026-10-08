"""
A stand-in Brain that streams canned events, so the app can be built and demoed
before the real brain is merged (CONTRACT §12). No LLM calls. Like the real brain it
keeps its records in the Store; conversation progress stays in memory.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from app.store.memory import InMemoryStore
from contract import (
    ActionDone,
    ActionResult,
    ActionUndone,
    Approval,
    ApprovalDecision,
    Ask,
    AuditEntry,
    AutonomyLevel,
    BusinessProfile,
    Error,
    Event,
    IncomingMessage,
    Lesson,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    Persona,
    PlannedAction,
    PostSocial,
    Progress,
    PromotionOffer,
    PromotionResponse,
    Say,
    SendEmail,
    Store,
    Task,
    TaskStatus,
    Team,
    TeamHired,
    TemplateInfo,
    Tools,
    Trust,
)

logger = logging.getLogger(__name__)

UNDO_WINDOW = timedelta(minutes=10)
MAX_REVISIONS = 2
PROMOTION_STREAK = 5
TOKENS_PER_STEP = 850
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
POST_LIMIT = 300
LADDER = list(AutonomyLevel)
ACTS_ALONE = (AutonomyLevel.ACT_AND_REPORT, AutonomyLevel.AUTONOMOUS)
CHIEF = Persona(name="Alex", role="Chief of staff")
BUSINESS_QUESTIONS = [
    "Hi! I'm Alex, your chief of staff. What's your business called?",
    "What do you sell?",
    "Who are your customers?",
]
FEEDBACK_MARKERS = ("stop ", "don't", "do not", "never", "always", "no more", "please use")
STEP_VERBS = {
    "researcher": "researching",
    "writer": "writing",
    "invoicer": "drafting the reminder",
}
POST_OPENERS = ["Big news:", "Quick update:", "Heads up:"]

@dataclass
class FakeTaskType:
    title: str
    steps: list[str]
    action: str | None
    start_level: AutonomyLevel = AutonomyLevel.ACT_AFTER_APPROVAL
    max_level: AutonomyLevel = AutonomyLevel.AUTONOMOUS

@dataclass
class FakeTemplate:
    name: str
    display_name: str
    description: str
    lead: Persona
    specialists: dict[str, Persona]
    task_types: dict[str, FakeTaskType]
    onboarding: list[tuple[str, str, list[str]]]
    default_plan: list[str]

    def info(self) -> TemplateInfo:
        return TemplateInfo(
            name=self.name,
            display_name=self.display_name,
            description=self.description,
            personas=[self.lead, *self.specialists.values()],
            task_types=list(self.task_types),
        )

TEMPLATES = {
    "marketing": FakeTemplate(
        name="marketing",
        display_name="Marketing",
        description="Posts, newsletters and competitor checks in your voice",
        lead=Persona(name="Maya", role="Marketing lead"),
        specialists={
            "writer": Persona(name="Leo", role="Writer"),
            "researcher": Persona(name="Sam", role="Researcher"),
        },
        task_types={
            "social_post": FakeTaskType("Bluesky post", ["writer"], "post_social"),
            "newsletter": FakeTaskType(
                "Newsletter",
                ["researcher", "writer"],
                "send_email",
                max_level=AutonomyLevel.ACT_AND_REPORT,
            ),
            "competitor_check": FakeTaskType(
                "Competitor check",
                ["researcher"],
                None,
                start_level=AutonomyLevel.ACT_AND_REPORT,
            ),
        },
        onboarding=[
            ("channels", "Which channels do you post on?", ["Bluesky", "Bluesky and LinkedIn"]),
            ("upcoming", "Any launch or event coming up?", ["Not right now"]),
            ("newsletter_to", "Which address should newsletters go to?", []),
        ],
        default_plan=["social_post", "newsletter"],
    ),
    "finance": FakeTemplate(
        name="finance",
        display_name="Finance",
        description="Friendly payment reminders so you never chase invoices",
        lead=Persona(name="Rita", role="Finance lead"),
        specialists={"invoicer": Persona(name="Ivo", role="Invoicer")},
        task_types={
            "invoice_reminder": FakeTaskType(
                "Payment reminder",
                ["invoicer"],
                "send_email",
                max_level=AutonomyLevel.ACT_AND_REPORT,
            ),
        },
        onboarding=[
            ("tone", "How firm should reminders be?", ["Gentle", "Direct"]),
            ("remind_after_days", "How many days after the due date should I remind?", ["3", "7"]),
        ],
        default_plan=["invoice_reminder"],
    ),
}

def _utcnow() -> datetime:
    return datetime.now(UTC)

def _new_id() -> str:
    return str(uuid4())

def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"

def _preview(action: PlannedAction) -> str:
    if isinstance(action, PostSocial):
        return action.text
    return f"To: {action.to}\nSubject: {action.subject}\n\n{action.body}"

def _is_feedback(text: str) -> bool:
    lowered = f"{text.lower().strip()} "
    return any(marker in lowered for marker in FEEDBACK_MARKERS)

def _next_level(level: AutonomyLevel, cap: AutonomyLevel) -> AutonomyLevel | None:
    index = LADDER.index(level)
    if index >= LADDER.index(cap):
        return None
    return LADDER[index + 1]

@dataclass
class _Thread:
    onboarding_step: int = 0
    pending_request: str | None = None

@dataclass
class _Ctx:
    team: Team
    template: FakeTemplate
    thread: _Thread

    @property
    def team_id(self) -> str:
        return self.team.team_id

    @property
    def lead(self) -> Persona:
        return self.template.lead

class FakeBrain:
    """
    Implements the Brain protocol with canned, deterministic behaviour.
    """

    def __init__(
        self,
        store: Store | None = None,
        *,
        tools: Tools | None = None,
        delay: float = 0.0,
        promotion_streak: int = PROMOTION_STREAK,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store or InMemoryStore()
        self._tools = tools
        self._delay = delay
        self._promotion_streak = promotion_streak
        self._clock = clock
        self._onboarding: dict[str, list[str]] = {}
        self._threads: dict[str, _Thread] = {}

    def list_templates(self) -> list[TemplateInfo]:
        return [template.info() for template in TEMPLATES.values()]

    def start_onboarding(self, business_id: str) -> AsyncIterator[Event]:
        return self._run(self._start_onboarding(business_id), None)

    def handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        return self._run(self._handle_message(msg), msg.team_id)

    def hire_team(self, business_id: str, template: str) -> AsyncIterator[Event]:
        return self._run(self._hire_team(business_id, template), None)

    def resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]:
        return self._run(self._resolve_approval(decision), None)

    def respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]:
        return self._run(self._respond_promotion(response), response.team_id)

    def undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]:
        return self._run(self._undo_action(business_id, action_id), None)

    async def _run(
        self, events: AsyncIterator[Event], team_id: str | None
    ) -> AsyncIterator[Event]:
        first = True
        try:
            async for event in events:
                if not first and self._delay:
                    await asyncio.sleep(self._delay)
                first = False
                yield event
        except Exception:
            logger.exception("Fake brain failed")
            yield Error(
                team_id=team_id,
                message="Something went wrong on my side. Please try again.",
                recoverable=True,
            )

    async def _context(self, business_id: str, team_id: str | None) -> _Ctx | None:
        if team_id is None:
            return None
        team = await self._store.get_team(team_id)
        if team is None or team.business_id != business_id or team.template not in TEMPLATES:
            return None
        thread = self._threads.setdefault(team_id, _Thread())
        return _Ctx(team=team, template=TEMPLATES[team.template], thread=thread)

    async def _trust(self, ctx: _Ctx, task_type: str) -> Trust:
        trust = await self._store.get_trust(ctx.team_id, task_type)
        if trust is None:
            level = ctx.template.task_types[task_type].start_level
            trust = Trust(
                team_id=ctx.team_id,
                task_type=task_type,
                level=level,
                promote_after=self._promotion_streak,
                updated_at=self._clock(),
            )
            await self._store.set_trust(trust)
        return trust

    async def _business_name(self, business_id: str) -> str:
        profile = await self._store.get_profile(business_id)
        return profile.name if profile else "us"

    async def _start_onboarding(self, business_id: str) -> AsyncIterator[Event]:
        if await self._store.get_profile(business_id) is not None:
            yield Say(
                team_id=None,
                persona=CHIEF,
                text="We're already set up. Hire a team with /hire marketing.",
            )
            return
        self._onboarding[business_id] = []
        yield Ask(team_id=None, persona=CHIEF, question=BUSINESS_QUESTIONS[0])

    async def _handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        if msg.team_id is None:
            async for event in self._company_message(msg):
                yield event
            return
        ctx = await self._context(msg.business_id, msg.team_id)
        if ctx is None:
            yield Error(team_id=msg.team_id, message="I don't know this team.", recoverable=False)
            return
        text = msg.text.strip()
        if not ctx.team.onboarded:
            events = self._team_onboarding(ctx, msg)
        elif ctx.thread.pending_request is not None:
            request = f"{ctx.thread.pending_request}\n{text}"
            ctx.thread.pending_request = None
            events = self._work(ctx, request)
        elif _is_feedback(text):
            events = self._learn_from_chat(ctx, msg)
        elif len(text.split()) < 4:
            ctx.thread.pending_request = text
            events = self._clarify(ctx)
        else:
            events = self._work(ctx, text)
        async for event in events:
            yield event

    async def _company_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        if await self._store.get_profile(msg.business_id) is not None:
            yield Say(
                team_id=None,
                persona=CHIEF,
                text="Hire a team with /hire marketing, then talk to it in its topic.",
            )
            return
        answers = self._onboarding.get(msg.business_id)
        if answers is None:
            async for event in self._start_onboarding(msg.business_id):
                yield event
            return
        answers.append(msg.text.strip())
        if len(answers) < len(BUSINESS_QUESTIONS):
            yield Ask(team_id=None, persona=CHIEF, question=BUSINESS_QUESTIONS[len(answers)])
            return
        name, what_you_sell, customers = answers
        await self._store.save_profile(
            BusinessProfile(
                business_id=msg.business_id,
                name=name,
                what_you_sell=what_you_sell,
                customers=customers,
            )
        )
        del self._onboarding[msg.business_id]
        yield Say(
            team_id=None,
            persona=CHIEF,
            text=f"Thanks, I know {name} well now. Hire your first team with /hire marketing.",
        )
        yield OnboardingComplete(scope="business", team_id=None)

    async def _hire_team(self, business_id: str, name: str) -> AsyncIterator[Event]:
        template = TEMPLATES.get(name)
        if template is None:
            message = f"There's no '{name}' team to hire."
            yield Error(team_id=None, message=message, recoverable=True)
            return
        now = self._clock()
        team = Team(
            team_id=_new_id(),
            business_id=business_id,
            template=name,
            display_name=template.display_name,
            created_at=now,
        )
        await self._store.save_team(team)
        for task_type, spec in template.task_types.items():
            trust = Trust(
                team_id=team.team_id,
                task_type=task_type,
                level=spec.start_level,
                promote_after=self._promotion_streak,
                updated_at=now,
            )
            await self._store.set_trust(trust)
        ctx = _Ctx(team=team, template=template, thread=_Thread())
        self._threads[team.team_id] = ctx.thread
        yield TeamHired(
            team_id=team.team_id,
            template=name,
            display_name=template.display_name,
            personas=template.info().personas,
        )
        yield Say(
            team_id=team.team_id,
            persona=ctx.lead,
            text=f"Hi, I'm {ctx.lead.name}. A few quick questions so we fit your business.",
        )
        yield self._onboarding_question(ctx)

    def _onboarding_question(self, ctx: _Ctx) -> Ask:
        _, question, quick_replies = ctx.template.onboarding[ctx.thread.onboarding_step]
        return Ask(
            team_id=ctx.team_id, persona=ctx.lead, question=question, quick_replies=quick_replies
        )

    async def _team_onboarding(self, ctx: _Ctx, msg: IncomingMessage) -> AsyncIterator[Event]:
        step = min(ctx.thread.onboarding_step, len(ctx.template.onboarding) - 1)
        key = ctx.template.onboarding[step][0]
        await self._save_fact(ctx, key, msg)
        ctx.thread.onboarding_step = step + 1
        if ctx.thread.onboarding_step < len(ctx.template.onboarding):
            yield self._onboarding_question(ctx)
            return
        ctx.team.onboarded = True
        await self._store.save_team(ctx.team)
        yield Say(
            team_id=ctx.team_id,
            persona=ctx.lead,
            text="Thanks, we're ready. Tell me what you need, like 'We launch Friday'.",
        )
        yield OnboardingComplete(scope="team", team_id=ctx.team_id)

    async def _save_fact(self, ctx: _Ctx, key: str, msg: IncomingMessage) -> None:
        for old in await self._store.list_lessons(ctx.team.business_id, ctx.team_id):
            if old.key == key and old.team_id == ctx.team_id:
                await self._store.save_lesson(old.model_copy(update={"active": False}))
        label = key.replace("_", " ").capitalize()
        await self._store.save_lesson(
            Lesson(
                lesson_id=_new_id(),
                business_id=ctx.team.business_id,
                team_id=ctx.team_id,
                kind="fact",
                key=key,
                text=f"{label}: {msg.text.strip()}",
                source="team_onboarding",
                source_ref=msg.message_id,
                created_at=self._clock(),
            )
        )

    async def _clarify(self, ctx: _Ctx) -> AsyncIterator[Event]:
        yield Ask(
            team_id=ctx.team_id,
            persona=ctx.lead,
            question="Can you tell me a bit more? What's it about, and when should it go out?",
            quick_replies=["It's for this week's launch", "Just a general update"],
        )

    async def _learn_from_chat(self, ctx: _Ctx, msg: IncomingMessage) -> AsyncIterator[Event]:
        text = msg.text.strip()
        business_wide = text.lower().startswith("we ")
        async for event in self._lesson(
            ctx, None, text, "chat", msg.message_id, business_wide=business_wide
        ):
            yield event

    async def _lesson(
        self,
        ctx: _Ctx,
        task: Task | None,
        text: str,
        source: Literal["edit", "reject", "chat"],
        source_ref: str,
        business_wide: bool = False,
    ) -> AsyncIterator[Event]:
        lesson = Lesson(
            lesson_id=_new_id(),
            business_id=ctx.team.business_id,
            team_id=None if business_wide else ctx.team_id,
            task_type=task.task_type if task and source == "edit" else None,
            kind="preference",
            text=text,
            source=source,
            source_ref=source_ref,
            created_at=self._clock(),
        )
        await self._store.save_lesson(lesson)
        yield LessonLearned(
            lesson_id=lesson.lesson_id,
            team_id=ctx.team_id,
            persona=ctx.lead,
            text=text,
            business_wide=business_wide,
        )
        yield Say(
            team_id=ctx.team_id,
            task_id=task.task_id if task else None,
            persona=ctx.lead,
            text="Got it, I'll remember that from now on.",
        )

    def _plan(self, ctx: _Ctx, request: str) -> list[str]:
        if "competitor" in request.lower() and "competitor_check" in ctx.template.task_types:
            return ["competitor_check"]
        return ctx.template.default_plan

    async def _work(self, ctx: _Ctx, request: str) -> AsyncIterator[Event]:
        plan = self._plan(ctx, request)
        titles = ", ".join(ctx.template.task_types[task_type].title.lower() for task_type in plan)
        yield Say(team_id=ctx.team_id, persona=ctx.lead, text=f"On it. Plan: {titles}.")
        tasks = []
        for task_type in plan:
            spec = ctx.template.task_types[task_type]
            now = self._clock()
            task = Task(
                task_id=_new_id(),
                business_id=ctx.team.business_id,
                team_id=ctx.team_id,
                task_type=task_type,
                title=_clip(request, 60),
                brief=request,
                status=TaskStatus.IN_PROGRESS,
                steps=spec.steps,
                created_at=now,
                updated_at=now,
            )
            await self._store.save_task(task)
            tasks.append(task)
            for index, step in enumerate(spec.steps):
                persona = ctx.template.specialists[step]
                verb = STEP_VERBS.get(step, "working")
                yield Progress(
                    team_id=ctx.team_id,
                    task_id=task.task_id,
                    persona=persona,
                    status=f"{persona.name} is {verb}…",
                )
                task.current_step = index
                task.tokens_used += TOKENS_PER_STEP
                task.updated_at = self._clock()
                await self._store.save_task(task)
            async for event in self._gate(ctx, task):
                yield event
        waiting = sum(task.status == TaskStatus.WAITING_APPROVAL for task in tasks)
        report = f"{waiting} draft(s) are waiting for your OK." if waiting else "All done."
        yield Say(team_id=ctx.team_id, persona=ctx.lead, text=report)

    async def _draft(self, task: Task) -> tuple[str, PlannedAction | None]:
        name = await self._business_name(task.business_id)
        brief = task.brief
        if task.task_type == "social_post":
            opener = POST_OPENERS[task.revisions % len(POST_OPENERS)]
            action = PostSocial(text=_clip(f"{opener} {brief}", POST_LIMIT))
        elif task.task_type == "newsletter":
            action = SendEmail(
                to=await self._newsletter_address(task),
                subject=f"News from {name}",
                body=f"Hi there,\n\n{brief}\n\nReply if you have questions.\n\n{name}",
            )
        elif task.task_type == "invoice_reminder":
            action = SendEmail(
                to="client@example.com",
                subject="Friendly reminder: invoice due",
                body=f"Hi,\n\nA quick reminder about the open invoice from {name}.\n\nThanks!",
            )
        else:
            report = f"Competitor check for {name}: two competitors posted launch offers this week."
            return report, None
        return _preview(action), action

    async def _save_status(self, task: Task, status: TaskStatus) -> None:
        task.status = status
        task.updated_at = self._clock()
        await self._store.save_task(task)

    async def _gate(self, ctx: _Ctx, task: Task) -> AsyncIterator[Event]:
        preview, action = await self._draft(task)
        level = (await self._trust(ctx, task.task_type)).level
        if level in ACTS_ALONE:
            if action is None:
                await self._save_status(task, TaskStatus.DONE)
                yield Say(
                    team_id=task.team_id, task_id=task.task_id, persona=ctx.lead, text=preview
                )
            else:
                yield await self._execute(task, action, approval_id=None)
            return
        planned_action = None if level == AutonomyLevel.DRAFT_ONLY else action
        approval = Approval(
            approval_id=_new_id(),
            business_id=task.business_id,
            team_id=task.team_id,
            task_id=task.task_id,
            task_type=task.task_type,
            preview=preview,
            planned_action=planned_action,
            check_confidence=round(0.9 - 0.05 * task.revisions, 2),
            created_at=self._clock(),
        )
        await self._store.save_approval(approval)
        await self._save_status(task, TaskStatus.WAITING_APPROVAL)
        yield NeedsApproval(
            approval_id=approval.approval_id,
            team_id=task.team_id,
            task_id=task.task_id,
            task_type=task.task_type,
            persona=ctx.lead,
            preview=preview,
            planned_action=planned_action,
            check_confidence=approval.check_confidence,
        )

    async def _newsletter_address(self, task: Task) -> str:
        for lesson in await self._store.list_lessons(task.business_id, task.team_id):
            if lesson.key == "newsletter_to":
                match = EMAIL.search(lesson.text)
                if match:
                    return match.group(0).lower()
        return "newsletter@example.com"

    async def _act(self, task: Task, action: PlannedAction) -> ActionResult:
        if self._tools is None:
            action_id = _new_id()
            if isinstance(action, PostSocial):
                rkey = action_id.replace("-", "")[:13]
                return ActionResult(
                    action_id=action_id,
                    ok=True,
                    url=f"https://bsky.app/profile/demo.bsky.social/post/{rkey}",
                    external_id=f"at://did:plc:demo/app.bsky.feed.post/{rkey}",
                )
            return ActionResult(action_id=action_id, ok=True)
        if isinstance(action, PostSocial):
            return await self._tools.post_social(task.business_id, action.text)
        return await self._tools.send_email(
            task.business_id, action.to, action.subject, action.body
        )

    async def _execute(
        self, task: Task, action: PlannedAction, approval_id: str | None
    ) -> ActionDone | Error:
        result = await self._act(task, action)
        now = self._clock()
        if isinstance(action, PostSocial):
            summary = "Posted to Bluesky" if result.ok else "Couldn't post to Bluesky"
            undo_until = now + UNDO_WINDOW if result.ok else None
        else:
            verb = "Sent" if result.ok else "Couldn't send"
            summary = f"{verb} the email to {action.to}"
            undo_until = None
        autonomous = approval_id is None
        await self._store.log_action(
            AuditEntry(
                action_id=result.action_id,
                business_id=task.business_id,
                team_id=task.team_id,
                task_id=task.task_id,
                tool=action.tool,
                summary=summary,
                result=result,
                autonomous=autonomous,
                approval_id=approval_id,
                undo_until=undo_until,
                at=now,
            )
        )
        if not result.ok:
            await self._save_status(task, TaskStatus.FAILED)
            message = f"{summary}: {result.error}"
            return Error(team_id=task.team_id, message=message, recoverable=True)
        await self._save_status(task, TaskStatus.DONE)
        return ActionDone(
            action_id=result.action_id,
            team_id=task.team_id,
            task_id=task.task_id,
            summary=summary,
            url=result.url,
            autonomous=autonomous,
            undo_until=undo_until,
        )

    async def _resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]:
        approval = await self._store.get_approval(decision.approval_id)
        if approval is None or approval.business_id != decision.business_id:
            yield Error(team_id=None, message="I can't find that draft.", recoverable=True)
            return
        if approval.status != "pending":
            yield Error(
                team_id=approval.team_id,
                message="That draft was already handled.",
                recoverable=True,
            )
            return
        ctx = await self._context(approval.business_id, approval.team_id)
        task = await self._store.get_task(approval.task_id)
        if ctx is None or task is None:
            message = "I can't find that task."
            yield Error(team_id=approval.team_id, message=message, recoverable=True)
            return
        if decision.decision == "approve":
            events = self._approve(ctx, task, approval)
        elif decision.decision == "edit":
            events = self._edit(ctx, task, approval, decision.edited_text)
        else:
            events = self._reject(ctx, task, approval, decision.reason)
        async for event in events:
            yield event

    async def _set_streak(self, ctx: _Ctx, task_type: str, streak: int) -> None:
        trust = await self._trust(ctx, task_type)
        trust.approval_streak = streak
        trust.updated_at = self._clock()
        await self._store.set_trust(trust)

    async def _resolve(self, approval: Approval, status: str, **fields: str | None) -> None:
        approval.status = status
        approval.resolved_at = self._clock()
        for name, value in fields.items():
            setattr(approval, name, value)
        await self._store.save_approval(approval)

    async def _approve(self, ctx: _Ctx, task: Task, approval: Approval) -> AsyncIterator[Event]:
        await self._resolve(approval, "approved")
        if approval.planned_action is None:
            await self._save_status(task, TaskStatus.DONE)
            yield Say(
                team_id=task.team_id,
                task_id=task.task_id,
                persona=ctx.lead,
                text="Great, marked as done.",
            )
        else:
            outcome = await self._execute(task, approval.planned_action, approval.approval_id)
            yield outcome
            if isinstance(outcome, Error):
                return
        trust = await self._trust(ctx, task.task_type)
        await self._set_streak(ctx, task.task_type, trust.approval_streak + 1)
        offer = await self._promotion_offer(ctx, task.task_type)
        if offer is not None:
            yield offer

    async def _promotion_offer(self, ctx: _Ctx, task_type: str) -> PromotionOffer | None:
        trust = await self._trust(ctx, task_type)
        cap = ctx.template.task_types[task_type].max_level
        proposed = _next_level(trust.level, cap)
        if trust.approval_streak < trust.promote_after or proposed is None:
            return None
        return PromotionOffer(
            team_id=ctx.team_id,
            task_type=task_type,
            persona=ctx.lead,
            current_level=trust.level,
            proposed_level=proposed,
            evidence=f"You approved my last {trust.approval_streak} drafts without edits.",
        )

    async def _edit(
        self, ctx: _Ctx, task: Task, approval: Approval, edited_text: str | None
    ) -> AsyncIterator[Event]:
        if not edited_text or not edited_text.strip():
            yield Error(
                team_id=task.team_id, message="Send me the edited version first.", recoverable=True
            )
            return
        action = approval.planned_action
        if isinstance(action, PostSocial) and len(edited_text) > POST_LIMIT:
            yield Error(
                team_id=task.team_id,
                message=f"That's {len(edited_text)} characters; Bluesky allows {POST_LIMIT}.",
                recoverable=True,
            )
            return
        await self._resolve(approval, "edited", edited_text=edited_text)
        if isinstance(action, PostSocial):
            yield await self._execute(task, PostSocial(text=edited_text), approval.approval_id)
        elif isinstance(action, SendEmail):
            edited = action.model_copy(update={"body": edited_text})
            yield await self._execute(task, edited, approval.approval_id)
        else:
            await self._save_status(task, TaskStatus.DONE)
        await self._set_streak(ctx, task.task_type, 0)
        lesson = "Match the founder's edits in wording and length"
        async for event in self._lesson(ctx, task, lesson, "edit", approval.approval_id):
            yield event

    async def _reject(
        self, ctx: _Ctx, task: Task, approval: Approval, reason: str | None
    ) -> AsyncIterator[Event]:
        await self._resolve(approval, "rejected", reason=reason)
        await self._set_streak(ctx, task.task_type, 0)
        if not reason:
            await self._save_status(task, TaskStatus.REJECTED)
            yield Say(
                team_id=task.team_id,
                task_id=task.task_id,
                persona=ctx.lead,
                text="Okay, dropped it.",
            )
            return
        async for event in self._lesson(ctx, task, reason, "reject", approval.approval_id):
            yield event
        if task.revisions >= MAX_REVISIONS:
            await self._save_status(task, TaskStatus.REJECTED)
            yield Say(
                team_id=task.team_id,
                task_id=task.task_id,
                persona=ctx.lead,
                text="I'm out of revisions on this one, so I'll drop it.",
            )
            return
        task.revisions += 1
        await self._save_status(task, TaskStatus.IN_PROGRESS)
        writer = ctx.template.specialists.get(task.steps[-1], ctx.lead)
        yield Progress(
            team_id=task.team_id,
            task_id=task.task_id,
            persona=writer,
            status=f"{writer.name} is revising…",
        )
        async for event in self._gate(ctx, task):
            yield event

    async def _respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]:
        ctx = await self._context(response.business_id, response.team_id)
        if ctx is None:
            yield Error(
                team_id=response.team_id, message="I don't know this team.", recoverable=False
            )
            return
        if response.task_type not in ctx.template.task_types:
            yield Error(
                team_id=response.team_id,
                message="This team doesn't do that kind of task.",
                recoverable=True,
            )
            return
        trust = await self._trust(ctx, response.task_type)
        cap = ctx.template.task_types[response.task_type].max_level
        proposed = _next_level(trust.level, cap)
        if response.accepted and proposed is not None:
            trust.level = proposed
            text = f"Thanks for the trust! {response.task_type} is now at '{proposed}'."
        else:
            text = "No problem, I'll keep asking before I act."
        trust.approval_streak = 0
        trust.updated_at = self._clock()
        await self._store.set_trust(trust)
        yield Say(team_id=response.team_id, persona=ctx.lead, text=text)

    async def _undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]:
        entry = await self._store.get_action(action_id)
        if entry is None or entry.business_id != business_id:
            yield Error(team_id=None, message="I can't find that action.", recoverable=True)
            return
        now = self._clock()
        if entry.undone_at is not None:
            message = "That was already undone."
        elif entry.undo_until is None:
            message = "That one can't be undone."
        elif now > entry.undo_until:
            message = "The 10-minute undo window has closed."
        else:
            message = None
        if message is not None:
            yield Error(team_id=entry.team_id, message=message, recoverable=True)
            return
        if self._tools is None:
            result = ActionResult(action_id=_new_id(), ok=True)
        else:
            result = await self._tools.delete_social(business_id, entry.result.external_id or "")
        summary = "Deleted the Bluesky post" if result.ok else "Couldn't delete the Bluesky post"
        await self._store.log_action(
            AuditEntry(
                action_id=result.action_id,
                business_id=business_id,
                team_id=entry.team_id,
                task_id=entry.task_id,
                tool="delete_social",
                summary=summary,
                result=result,
                autonomous=False,
                at=now,
            )
        )
        if not result.ok:
            message = f"{summary}: {result.error}"
            yield Error(team_id=entry.team_id, message=message, recoverable=True)
            return
        await self._store.log_action(entry.model_copy(update={"undone_at": now}))
        ctx = await self._context(business_id, entry.team_id)
        task = await self._store.get_task(entry.task_id)
        if ctx is not None and task is not None:
            await self._set_streak(ctx, task.task_type, 0)
        yield ActionUndone(action_id=action_id, team_id=entry.team_id, summary=summary)
