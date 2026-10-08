"""
A stand-in Brain that streams canned events, so the app can be built and demoed
before the real brain is merged (CONTRACT §12). No LLM calls, state lives in memory.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    Task,
    TaskStatus,
    Team,
    TeamHired,
    TemplateInfo,
    Trust,
)

logger = logging.getLogger(__name__)

UNDO_WINDOW = timedelta(minutes=10)
MAX_REVISIONS = 2
PROMOTION_STREAK = 5
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
    onboarding: list[tuple[str, list[str]]]
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
            ("Which channels do you post on?", ["Bluesky", "Bluesky and LinkedIn"]),
            ("Any launch or event coming up?", ["Not right now"]),
            ("Which address should newsletters go to?", []),
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
            ("How firm should reminders be?", ["Gentle", "Direct"]),
            ("How many days after the due date should I remind?", ["3", "7", "14"]),
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
class _Business:
    answers: list[str] = field(default_factory=list)
    profile: BusinessProfile | None = None

@dataclass
class _Team:
    team: Team
    template: FakeTemplate
    trust: dict[str, Trust]
    onboarding_step: int = 0
    pending_request: str | None = None

class FakeBrain:
    """
    Implements the Brain protocol with canned, deterministic behaviour.
    """

    def __init__(
        self,
        delay: float = 0.0,
        promotion_streak: int = PROMOTION_STREAK,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._delay = delay
        self._promotion_streak = promotion_streak
        self._clock = clock
        self._businesses: dict[str, _Business] = {}
        self._teams: dict[str, _Team] = {}
        self._tasks: dict[str, Task] = {}
        self._approvals: dict[str, Approval] = {}
        self._actions: dict[str, AuditEntry] = {}

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

    async def _start_onboarding(self, business_id: str) -> AsyncIterator[Event]:
        business = self._businesses.setdefault(business_id, _Business())
        if business.profile is not None:
            yield Say(
                team_id=None,
                persona=CHIEF,
                text="We're already set up. Hire a team with /hire marketing.",
            )
            return
        business.answers = []
        yield Ask(team_id=None, persona=CHIEF, question=BUSINESS_QUESTIONS[0])

    async def _handle_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        if msg.team_id is None:
            async for event in self._company_message(msg):
                yield event
            return
        team = self._teams.get(msg.team_id)
        if team is None or team.team.business_id != msg.business_id:
            yield Error(team_id=msg.team_id, message="I don't know this team.", recoverable=False)
            return
        text = msg.text.strip()
        if not team.team.onboarded:
            events = self._team_onboarding(team)
        elif team.pending_request is not None:
            request = f"{team.pending_request}\n{text}"
            team.pending_request = None
            events = self._work(team, request)
        elif _is_feedback(text):
            events = self._learn_from_chat(team, text)
        elif len(text.split()) < 4:
            team.pending_request = text
            events = self._clarify(team)
        else:
            events = self._work(team, text)
        async for event in events:
            yield event

    async def _company_message(self, msg: IncomingMessage) -> AsyncIterator[Event]:
        business = self._businesses.get(msg.business_id)
        if business is None:
            async for event in self._start_onboarding(msg.business_id):
                yield event
            return
        if business.profile is not None:
            yield Say(
                team_id=None,
                persona=CHIEF,
                text="Hire a team with /hire marketing, then talk to it in its topic.",
            )
            return
        business.answers.append(msg.text.strip())
        if len(business.answers) < len(BUSINESS_QUESTIONS):
            question = BUSINESS_QUESTIONS[len(business.answers)]
            yield Ask(team_id=None, persona=CHIEF, question=question)
            return
        name, what_you_sell, customers = business.answers
        business.profile = BusinessProfile(
            business_id=msg.business_id,
            name=name,
            what_you_sell=what_you_sell,
            customers=customers,
        )
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
        team_id = _new_id()
        trust = {
            task_type: Trust(
                team_id=team_id, task_type=task_type, level=spec.start_level, updated_at=now
            )
            for task_type, spec in template.task_types.items()
        }
        team = Team(
            team_id=team_id,
            business_id=business_id,
            template=name,
            display_name=template.display_name,
            created_at=now,
        )
        self._teams[team_id] = _Team(team=team, template=template, trust=trust)
        yield TeamHired(
            team_id=team_id,
            template=name,
            display_name=template.display_name,
            personas=template.info().personas,
        )
        yield Say(
            team_id=team_id,
            persona=template.lead,
            text=f"Hi, I'm {template.lead.name}. A few quick questions so we fit your business.",
        )
        yield self._onboarding_question(self._teams[team_id])

    def _onboarding_question(self, team: _Team) -> Ask:
        question, quick_replies = team.template.onboarding[team.onboarding_step]
        return Ask(
            team_id=team.team.team_id,
            persona=team.template.lead,
            question=question,
            quick_replies=quick_replies,
        )

    async def _team_onboarding(self, team: _Team) -> AsyncIterator[Event]:
        team.onboarding_step += 1
        if team.onboarding_step < len(team.template.onboarding):
            yield self._onboarding_question(team)
            return
        team.team.onboarded = True
        yield Say(
            team_id=team.team.team_id,
            persona=team.template.lead,
            text="Thanks, we're ready. Tell me what you need, like 'We launch Friday'.",
        )
        yield OnboardingComplete(scope="team", team_id=team.team.team_id)

    async def _clarify(self, team: _Team) -> AsyncIterator[Event]:
        yield Ask(
            team_id=team.team.team_id,
            persona=team.template.lead,
            question="Can you tell me a bit more? What's it about, and when should it go out?",
            quick_replies=["It's for this week's launch", "Just a general update"],
        )

    async def _learn_from_chat(self, team: _Team, text: str) -> AsyncIterator[Event]:
        yield LessonLearned(
            lesson_id=_new_id(),
            team_id=team.team.team_id,
            persona=team.template.lead,
            text=text,
            business_wide=text.lower().startswith("we "),
        )
        yield Say(
            team_id=team.team.team_id,
            persona=team.template.lead,
            text="Got it, I'll keep that in mind from now on.",
        )

    def _plan(self, team: _Team, request: str) -> list[str]:
        if "competitor" in request.lower() and "competitor_check" in team.template.task_types:
            return ["competitor_check"]
        return team.template.default_plan

    async def _work(self, team: _Team, request: str) -> AsyncIterator[Event]:
        template = team.template
        team_id = team.team.team_id
        plan = self._plan(team, request)
        titles = ", ".join(template.task_types[task_type].title.lower() for task_type in plan)
        yield Say(team_id=team_id, persona=template.lead, text=f"On it. Plan: {titles}.")
        tasks = []
        for task_type in plan:
            spec = template.task_types[task_type]
            now = self._clock()
            task = Task(
                task_id=_new_id(),
                business_id=team.team.business_id,
                team_id=team_id,
                task_type=task_type,
                title=spec.title,
                brief=request,
                status=TaskStatus.IN_PROGRESS,
                steps=spec.steps,
                created_at=now,
                updated_at=now,
            )
            self._tasks[task.task_id] = task
            tasks.append(task)
            for index, step in enumerate(spec.steps):
                task.current_step = index
                persona = template.specialists[step]
                verb = STEP_VERBS.get(step, "working")
                yield Progress(
                    team_id=team_id,
                    task_id=task.task_id,
                    persona=persona,
                    status=f"{persona.name} is {verb}…",
                )
            async for event in self._gate(team, task):
                yield event
        waiting = sum(task.status == TaskStatus.WAITING_APPROVAL for task in tasks)
        report = f"{waiting} draft(s) are waiting for your OK." if waiting else "All done."
        yield Say(team_id=team_id, persona=template.lead, text=report)

    def _draft(self, task: Task) -> tuple[str, PlannedAction | None]:
        business = self._businesses.get(task.business_id)
        name = business.profile.name if business and business.profile else "us"
        brief = task.brief
        if task.task_type == "social_post":
            opener = POST_OPENERS[task.revisions % len(POST_OPENERS)]
            action = PostSocial(text=_clip(f"{opener} {brief}", POST_LIMIT))
        elif task.task_type == "newsletter":
            action = SendEmail(
                to="newsletter@example.com",
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

    async def _gate(self, team: _Team, task: Task) -> AsyncIterator[Event]:
        preview, action = self._draft(task)
        level = team.trust[task.task_type].level
        if level in ACTS_ALONE:
            if action is None:
                task.status = TaskStatus.DONE
                yield Say(
                    team_id=task.team_id,
                    task_id=task.task_id,
                    persona=team.template.lead,
                    text=preview,
                )
            else:
                yield self._execute(team, task, action, approval_id=None)
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
        self._approvals[approval.approval_id] = approval
        task.status = TaskStatus.WAITING_APPROVAL
        yield NeedsApproval(
            approval_id=approval.approval_id,
            team_id=task.team_id,
            task_id=task.task_id,
            task_type=task.task_type,
            persona=team.template.lead,
            preview=preview,
            planned_action=planned_action,
            check_confidence=approval.check_confidence,
        )

    def _execute(
        self, team: _Team, task: Task, action: PlannedAction, approval_id: str | None
    ) -> ActionDone:
        now = self._clock()
        action_id = _new_id()
        if isinstance(action, PostSocial):
            rkey = action_id.replace("-", "")[:13]
            summary = "Posted to Bluesky"
            url = f"https://bsky.app/profile/demo.bsky.social/post/{rkey}"
            external_id = f"at://did:plc:demo/app.bsky.feed.post/{rkey}"
            undo_until = now + UNDO_WINDOW
        else:
            summary = f"Sent the email to {action.to}"
            url = external_id = undo_until = None
        autonomous = approval_id is None
        self._actions[action_id] = AuditEntry(
            action_id=action_id,
            business_id=task.business_id,
            team_id=task.team_id,
            task_id=task.task_id,
            tool=action.tool,
            summary=summary,
            result=ActionResult(action_id=action_id, ok=True, url=url, external_id=external_id),
            autonomous=autonomous,
            approval_id=approval_id,
            undo_until=undo_until,
            at=now,
        )
        task.status = TaskStatus.DONE
        task.updated_at = now
        return ActionDone(
            action_id=action_id,
            team_id=task.team_id,
            task_id=task.task_id,
            summary=summary,
            url=url,
            autonomous=autonomous,
            undo_until=undo_until,
        )

    async def _resolve_approval(self, decision: ApprovalDecision) -> AsyncIterator[Event]:
        approval = self._approvals.get(decision.approval_id)
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
        team = self._teams[approval.team_id]
        task = self._tasks[approval.task_id]
        if decision.decision == "approve":
            events = self._approve(team, task, approval)
        elif decision.decision == "edit":
            events = self._edit(team, task, approval, decision.edited_text)
        else:
            events = self._reject(team, task, approval, decision.reason)
        async for event in events:
            yield event

    def _set_streak(self, team: _Team, task_type: str, streak: int) -> None:
        trust = team.trust[task_type]
        trust.approval_streak = streak
        trust.updated_at = self._clock()

    def _resolve(self, approval: Approval, status: str, **fields: str | None) -> None:
        approval.status = status
        approval.resolved_at = self._clock()
        for name, value in fields.items():
            setattr(approval, name, value)

    async def _approve(self, team: _Team, task: Task, approval: Approval) -> AsyncIterator[Event]:
        self._resolve(approval, "approved")
        if approval.planned_action is None:
            task.status = TaskStatus.DONE
            yield Say(
                team_id=task.team_id,
                task_id=task.task_id,
                persona=team.template.lead,
                text="Great, marked as done.",
            )
        else:
            yield self._execute(team, task, approval.planned_action, approval.approval_id)
        trust = team.trust[task.task_type]
        self._set_streak(team, task.task_type, trust.approval_streak + 1)
        offer = self._promotion_offer(team, task.task_type)
        if offer is not None:
            yield offer

    def _promotion_offer(self, team: _Team, task_type: str) -> PromotionOffer | None:
        trust = team.trust[task_type]
        cap = team.template.task_types[task_type].max_level
        proposed = _next_level(trust.level, cap)
        if trust.approval_streak < self._promotion_streak or proposed is None:
            return None
        return PromotionOffer(
            team_id=team.team.team_id,
            task_type=task_type,
            persona=team.template.lead,
            current_level=trust.level,
            proposed_level=proposed,
            evidence=f"You approved my last {trust.approval_streak} drafts without edits.",
        )

    async def _edit(
        self, team: _Team, task: Task, approval: Approval, edited_text: str | None
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
        self._resolve(approval, "edited", edited_text=edited_text)
        if isinstance(action, PostSocial):
            yield self._execute(team, task, PostSocial(text=edited_text), approval.approval_id)
        elif isinstance(action, SendEmail):
            edited = action.model_copy(update={"body": edited_text})
            yield self._execute(team, task, edited, approval.approval_id)
        else:
            task.status = TaskStatus.DONE
        self._set_streak(team, task.task_type, 0)
        lesson = "Match the founder's edits in wording and length"
        async for event in self._lesson(team, task, lesson):
            yield event

    async def _reject(
        self, team: _Team, task: Task, approval: Approval, reason: str | None
    ) -> AsyncIterator[Event]:
        self._resolve(approval, "rejected", reason=reason)
        self._set_streak(team, task.task_type, 0)
        lead = team.template.lead
        if not reason:
            task.status = TaskStatus.REJECTED
            yield Say(
                team_id=task.team_id, task_id=task.task_id, persona=lead, text="Okay, dropped it."
            )
            return
        async for event in self._lesson(team, task, reason):
            yield event
        if task.revisions >= MAX_REVISIONS:
            task.status = TaskStatus.REJECTED
            yield Say(
                team_id=task.team_id,
                task_id=task.task_id,
                persona=lead,
                text="I'm out of revisions on this one, so I'll drop it.",
            )
            return
        task.revisions += 1
        task.status = TaskStatus.IN_PROGRESS
        writer = team.template.specialists.get(task.steps[-1], lead)
        yield Progress(
            team_id=task.team_id,
            task_id=task.task_id,
            persona=writer,
            status=f"{writer.name} is revising…",
        )
        async for event in self._gate(team, task):
            yield event

    async def _lesson(self, team: _Team, task: Task, text: str) -> AsyncIterator[Event]:
        yield LessonLearned(
            lesson_id=_new_id(),
            team_id=task.team_id,
            persona=team.template.lead,
            text=text,
            business_wide=False,
        )
        yield Say(
            team_id=task.team_id,
            task_id=task.task_id,
            persona=team.template.lead,
            text="Got it, I'll remember that next time.",
        )

    async def _respond_promotion(self, response: PromotionResponse) -> AsyncIterator[Event]:
        team = self._teams.get(response.team_id)
        if team is None or team.team.business_id != response.business_id:
            yield Error(
                team_id=response.team_id, message="I don't know this team.", recoverable=False
            )
            return
        trust = team.trust.get(response.task_type)
        if trust is None:
            yield Error(
                team_id=response.team_id,
                message="This team doesn't do that kind of task.",
                recoverable=True,
            )
            return
        cap = team.template.task_types[response.task_type].max_level
        proposed = _next_level(trust.level, cap)
        if response.accepted and proposed is not None:
            trust.level = proposed
            text = f"Thanks for the trust! {response.task_type} is now at '{proposed}'."
        else:
            text = "No problem, I'll keep asking before I act."
        self._set_streak(team, response.task_type, 0)
        yield Say(team_id=response.team_id, persona=team.template.lead, text=text)

    async def _undo_action(self, business_id: str, action_id: str) -> AsyncIterator[Event]:
        entry = self._actions.get(action_id)
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
        delete_id = _new_id()
        summary = "Deleted the Bluesky post"
        self._actions[delete_id] = AuditEntry(
            action_id=delete_id,
            business_id=business_id,
            team_id=entry.team_id,
            task_id=entry.task_id,
            tool="delete_social",
            summary=summary,
            result=ActionResult(action_id=delete_id, ok=True),
            autonomous=False,
            at=now,
        )
        entry.undone_at = now
        task = self._tasks[entry.task_id]
        self._set_streak(self._teams[entry.team_id], task.task_type, 0)
        yield ActionUndone(action_id=action_id, team_id=entry.team_id, summary=summary)
