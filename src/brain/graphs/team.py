import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from brain.check import run_check
from brain.common import new_id
from brain.context import build_context
from brain.deps import Deps
from brain.flows.actions import execute_action
from brain.flows.approvals import MAX_CHECKS
from brain.flows.autonomy import gate
from brain.graphs.specialist import build_specialist_graph, run_specialist
from brain.helpers.decide import decide
from brain.helpers.images import ImageError
from brain.prompts.lead import (
    ABOUT_IMAGE,
    HAS_FEEDBACK,
    IMAGE_QUESTION,
    IMAGE_REPLIES,
    IS_CLEAR,
    MENTIONS_IMAGE,
    NAMES_CHANNELS,
    ONE_OFF,
    STOPS_SCHEDULE,
    WANTS_IMAGE,
    WANTS_SCHEDULE,
    WEEKDAYS,
    LeadPlan,
    ScheduleDraft,
    TaskPlan,
    cadence_words,
    changes_question,
    needs_question,
    plan_messages,
    reply_messages,
    report_text,
    schedule_messages,
    triage_state,
    which_question,
)
from brain.reasoning import NEEDS_REASONING, needs_reasoning, wants_reasoning
from brain.templates.models import SpecialistSpec, TaskTypeSpec, Template
from brain.templates.registries import MAX_IMAGES, OUTPUTS
from brain.tools import MAX_IMAGE_CALLS, is_plan
from brain.usage import enter_task
from contract import (
    ActionDone,
    Approval,
    Ask,
    AutonomyLevel,
    Cadence,
    Error,
    Event,
    FileRef,
    Lesson,
    NeedsApproval,
    OnboardingComplete,
    Persona,
    Progress,
    Say,
    Schedule,
    ScheduleSaved,
    Task,
    TaskStatus,
    Team,
    Trust,
)

log = logging.getLogger(__name__)

Phase = Literal["work", "check", "gate", "done"]

class TaskState(BaseModel):
    task: Task
    phase: Phase = "work"
    outputs: dict[str, dict] = {}
    feedback: list[str] = []
    check_confidence: float | None = None
    reasoning: bool = False
    media: dict[str, FileRef] = {}
    prompts: dict[str, str] = {}
    aspects: dict[str, str] = {}
    rendered: bool = False
    redraw: bool = False
    checks: int = 0

class TeamState(TypedDict, total=False):
    business_id: str
    team_id: str
    template: str
    request: str
    media: dict[str, dict]
    past_tasks: dict[str, dict]
    message_id: str | None
    revise: str | None
    feedback: str | None
    image_feedback: bool | None
    suggest: list[str]
    suggested_teams: list[str]
    schedule_id: str | None
    schedule_history: dict[str, list[str]]
    schedule_mode: str | None
    schedule_draft: dict | None
    schedule_target: str | None
    assumptions: list[str]
    onboarded: bool
    reply_reason: str | None
    reasoning: bool
    ask_channels: bool
    ask_image: bool
    history: list[str]
    routed: list[str]
    plan_question: str | None
    clarified: int
    onboarding_answers: dict[str, str]
    tasks: dict[str, dict]
    order: list[str]
    current: str | None
    next: str
    tokens_used: int

LearnHook = Callable[[TeamState, str], AsyncIterator[Event]]

MAX_HISTORY = 5
MAX_PAST_TASKS = 20
MAX_SCHEDULE_RUNS = 3

def new_request(
    team: Team, text: str, message_id: str | None, media: dict[str, FileRef] | None = None
) -> TeamState:
    """
    The input for a new run on a team's thread. Resets everything per request; keeps the
    onboarding answers, the recent requests and the recent tasks already in the checkpoint.
    """
    return {
        "business_id": team.business_id,
        "team_id": team.team_id,
        "template": team.template,
        "request": text,
        "media": {file_id: ref.model_dump(mode="json") for file_id, ref in (media or {}).items()},
        "message_id": message_id,
        "revise": None,
        "feedback": None,
        "image_feedback": None,
        "suggest": [],
        "schedule_id": None,
        "schedule_mode": None,
        "schedule_draft": None,
        "schedule_target": None,
        "assumptions": [],
        "reply_reason": None,
        "reasoning": False,
        "ask_channels": False,
        "ask_image": False,
        "routed": [],
        "plan_question": None,
        "clarified": 0,
        "tasks": {},
        "order": [],
        "current": None,
        "tokens_used": 0,
    }

def build_team_graph(
    deps: Deps, checkpointer, learn: LearnHook | None = None
) -> CompiledStateGraph:
    """
    The orchestrator every team runs. Its shape never changes; the template in the state decides
    the questions, the task types and the steps.
    """
    specialist_graph = build_specialist_graph(deps)
    threshold = deps.settings.decide_threshold

    def template_of(state: TeamState) -> Template:
        return deps.templates[state["template"]]

    def emit(event: Event) -> None:
        get_stream_writer()(event)

    def load(state: TeamState, task_id: str) -> TaskState:
        return TaskState.model_validate(state["tasks"][task_id])

    def put(state: TeamState, ts: TaskState) -> dict[str, dict]:
        return {**state["tasks"], ts.task.task_id: ts.model_dump(mode="json")}

    async def save(task: Task, **changes) -> Task:
        updated = task.model_copy(update={**changes, "updated_at": deps.clock()})
        await deps.store.save_task(updated)
        return updated

    async def fail(state: TeamState, ts: TaskState, message: str) -> TaskState:
        task = await save(ts.task, status=TaskStatus.FAILED)
        emit(Error(team_id=state["team_id"], message=message, recoverable=True))
        return ts.model_copy(update={"task": task, "phase": "done"})

    async def entry(state: TeamState) -> dict:
        team = await deps.store.get_team(state["team_id"])
        update: dict = {"onboarded": bool(team and team.onboarded), "current": None}
        if state.get("revise"):
            ts = await revision_task(state)
            if ts is None:
                update["revise"] = None
            else:
                update["tasks"] = {ts.task.task_id: ts.model_dump(mode="json")}
                update["order"] = [ts.task.task_id]
        return update

    async def revision_task(state: TeamState) -> TaskState | None:
        approval = await deps.store.get_approval(state["revise"])
        task = await deps.store.get_task(approval.task_id) if approval else None
        if approval is None or task is None:
            return None
        rerun = cross_step(task.steps) if state.get("image_feedback") else None
        task = await save(
            task,
            revisions=task.revisions + 1,
            current_step=rerun if rerun is not None else last_own_step(task.steps),
            status=TaskStatus.IN_PROGRESS,
        )
        feedback = [
            f"The founder rejected your last draft:\n{approval.preview}\n"
            f"Their reason: {state.get('feedback') or 'none given'}"
        ]
        reasoning, _ = await needs_reasoning(
            deps, f"Task: {task.title}\nBrief: {task.brief}\n\n{feedback[0]}"
        )
        past = (state.get("past_tasks") or {}).get(task.task_id)
        redraw = rerun is not None
        if past is None:
            return TaskState(task=task, feedback=feedback, reasoning=reasoning, redraw=redraw)
        return TaskState.model_validate(past).model_copy(
            update={
                "task": task,
                "phase": "work",
                "feedback": feedback,
                "check_confidence": None,
                "reasoning": reasoning,
                "redraw": redraw,
                "checks": 0,
            }
        )

    def after_entry(state: TeamState) -> str:
        if state.get("revise") and state.get("order"):
            return "dispatch"
        if not state["onboarded"]:
            return "onboard_intro" if template_of(state).onboarding else "onboard_done"
        return "triage"

    async def onboard_intro(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        crew = ", ".join(
            f"{s.persona.name} ({s.persona.role})" for s in template.specialists.values()
        )
        emit(
            Say(
                team_id=state["team_id"],
                persona=lead,
                text=(
                    f"Hi! I'm {lead.name}, your {lead.role}, working with {crew}. "
                    "A few quick questions so we fit your business."
                ),
            )
        )
        return {}

    def pending_questions(state: TeamState) -> list[str]:
        answers = state.get("onboarding_answers") or {}
        return [key for key in template_of(state).onboarding if key not in answers]

    async def onboard(state: TeamState) -> dict:
        pending = pending_questions(state)
        if not pending:
            return {}
        template = template_of(state)
        key = pending[0]
        spec = template.onboarding[key]
        question = spec.question
        ask = Ask(
            team_id=state["team_id"],
            persona=template.lead.persona,
            question=question,
            quick_replies=spec.quick_replies,
        )
        answer = str(interrupt(ask.model_dump(mode="json")))
        await save_fact(state, key, question, answer)
        return {"onboarding_answers": {**(state.get("onboarding_answers") or {}), key: answer}}

    async def save_fact(state: TeamState, key: str, question: str, answer: str) -> None:
        existing = await deps.store.list_lessons(state["business_id"], state["team_id"])
        for lesson in existing:
            if lesson.key == key and lesson.team_id == state["team_id"]:
                await deps.store.save_lesson(lesson.model_copy(update={"active": False}))
        await deps.store.save_lesson(
            Lesson(
                lesson_id=new_id(),
                business_id=state["business_id"],
                team_id=state["team_id"],
                kind="fact",
                key=key,
                text=f"{question} {answer}",
                source="team_onboarding",
                created_at=deps.clock(),
            )
        )

    def after_onboard(state: TeamState) -> str:
        return "onboard" if pending_questions(state) else "onboard_done"

    async def onboard_done(state: TeamState) -> dict:
        team = await deps.store.get_team(state["team_id"])
        if team is not None:
            await deps.store.save_team(team.model_copy(update={"onboarded": True}))
        lead = template_of(state).lead.persona
        emit(OnboardingComplete(scope="team", team_id=state["team_id"]))
        emit(
            Say(
                team_id=state["team_id"],
                persona=lead,
                text="Thanks, that's all I need. What should we work on first?",
            )
        )
        return {"onboarded": True}

    def route_questions(template: Template) -> dict:
        return {
            f"needs_{task_type}": needs_question(spec)
            for task_type, spec in template.task_types.items()
        }

    def routed_from(template: Template, decisions) -> list[str]:
        routed = [
            task_type
            for task_type in template.task_types
            if decisions[f"needs_{task_type}"].accepts("yes", deps.settings.route_threshold)
        ]
        return routed[: template.limits.max_tasks_per_request]

    async def triage(state: TeamState) -> dict:
        """
        One Jev call for a new message: does it carry feedback, and which task types it needs.
        No task type means no work: the lead just replies.
        """
        template = template_of(state)
        scheduled = bool(state.get("schedule_id"))
        questions = {"needs_reasoning": NEEDS_REASONING, **route_questions(template)}
        if template.channel_question:
            questions["names_channels"] = NAMES_CHANNELS
        active: list[Schedule] = []
        if not scheduled:
            questions["has_feedback"] = HAS_FEEDBACK
            questions["one_off"] = ONE_OFF
            questions["wants_schedule"] = WANTS_SCHEDULE
            active = await active_schedules(state)
            if active:
                questions["changes_schedule"] = changes_question(active)
        decisions = await decide(deps, questions, triage_state(template, state["request"]))
        reasoning = wants_reasoning(deps, decisions)
        tokens = state.get("tokens_used", 0) + decisions.tokens
        lasting = not scheduled and not decisions["one_off"].accepts("yes", threshold)
        if lasting and learn is not None and decisions["has_feedback"].accepts("yes", threshold):
            async for event in learn({**state, "reasoning": reasoning}, state["request"]):
                emit(event)
        if active and decisions["changes_schedule"].accepts("yes", threshold):
            return {"schedule_mode": "change", "reasoning": reasoning, "tokens_used": tokens}
        if not scheduled and decisions["wants_schedule"].accepts("yes", threshold):
            return {"schedule_mode": "new", "reasoning": reasoning, "tokens_used": tokens}
        routed = routed_from(template, decisions)
        goes_out = set(routed) & set(template.channel_task_types())
        ask_channels = bool(
            template.channel_question
            and goes_out
            and not decisions["names_channels"].accepts("yes", threshold)
        )
        assumptions = list(state.get("assumptions") or [])
        if scheduled and ask_channels:
            ask_channels = False
            made = ", ".join(task_type.replace("_", " ") for task_type in routed)
            assumptions.append(
                f"I didn't stop to ask where this goes out; I went with: {made}. Tell me if you "
                "want other channels too."
            )
        return {
            "routed": routed,
            "ask_channels": ask_channels,
            "assumptions": assumptions,
            "reasoning": reasoning,
            "reply_reason": "chat",
            "tokens_used": tokens,
        }

    def after_triage(state: TeamState) -> str:
        if state.get("schedule_mode") == "new":
            return "schedule"
        if state.get("schedule_mode") == "change":
            return "change_schedule"
        if state.get("ask_channels"):
            return "channels"
        return "plan" if state["routed"] else "reply"

    async def active_schedules(state: TeamState) -> list[Schedule]:
        """
        The team's active schedules. A store that can't list them never breaks the work.
        """
        try:
            schedules = await deps.store.list_schedules(state["business_id"], state["team_id"])
        except Exception as error:
            log.warning("Couldn't list schedules, going on without them: %s", error)
            return []
        return [schedule for schedule in schedules if schedule.active]

    async def extract_schedule(
        state: TeamState, current: Schedule | None
    ) -> tuple[ScheduleDraft | None, int]:
        template = template_of(state)
        try:
            result = await deps.llm.structured(
                deps.settings.model_lead,
                schedule_messages(template, state["request"], current),
                ScheduleDraft,
                reasoning=False,
            )
        except Exception as error:
            log.warning("Schedule extraction failed: %s", error)
            return None, 0
        return result.value, result.tokens

    def lead_says(state: TeamState, text: str) -> None:
        emit(Say(team_id=state["team_id"], persona=template_of(state).lead.persona, text=text))

    async def schedule(state: TeamState) -> dict:
        """
        A request to repeat something: read when and what into a draft. Nothing runs now; the
        schedule card's Run now does that.
        """
        draft, used = await extract_schedule(state, None)
        tokens = state.get("tokens_used", 0) + used
        if draft is None:
            example = '"every Monday at 9, a newsletter idea"'
            lead_says(state, f"I couldn't set that up. Tell me what and when, like {example}.")
            return {"schedule_draft": None, "tokens_used": tokens}
        return {
            "schedule_draft": draft.model_dump(),
            "schedule_target": None,
            "tokens_used": tokens,
        }

    async def change_schedule(state: TeamState) -> dict:
        """
        Stop or change one of the team's active schedules, keeping its id.
        """
        active = await active_schedules(state)
        questions = {"stops_schedule": STOPS_SCHEDULE}
        if len(active) > 1:
            questions["which_schedule"] = which_question(active)
        listed = "\n".join(f"- {s.title}: {cadence_words(s.cadence)}" for s in active)
        decisions = await decide(
            deps, questions, f"Schedules:\n{listed}\n\nThe founder's message:\n{state['request']}"
        )
        tokens = state.get("tokens_used", 0) + decisions.tokens
        target = active[0] if len(active) == 1 else None
        if len(active) > 1 and decisions["which_schedule"].accepts(
            decisions["which_schedule"].choice, threshold
        ):
            choice = decisions["which_schedule"].choice
            if choice.startswith("s") and choice[1:].isdigit():
                target = active[int(choice[1:]) - 1]
        if target is None:
            lead_says(state, "Which schedule do you mean? Tell me its name and what to change.")
            return {"schedule_draft": None, "tokens_used": tokens}
        if decisions["stops_schedule"].accepts("yes", threshold):
            stopped = target.model_copy(update={"active": False})
            await deps.store.save_schedule(stopped)
            emit(
                ScheduleSaved(
                    team_id=state["team_id"],
                    persona=template_of(state).lead.persona,
                    schedule=stopped,
                )
            )
            lead_says(state, f"Stopped “{stopped.title}”.")
            return {"schedule_draft": None, "tokens_used": tokens}
        draft, used = await extract_schedule(state, target)
        if draft is None:
            lead_says(state, f"I couldn't tell what to change in “{target.title}”.")
            return {"schedule_draft": None, "tokens_used": tokens + used}
        return {
            "schedule_draft": draft.model_dump(),
            "schedule_target": target.schedule_id,
            "tokens_used": tokens + used,
        }

    async def needs_day(state: TeamState) -> bool:
        draft = ScheduleDraft.model_validate(state["schedule_draft"])
        target = state.get("schedule_target")
        current = await deps.store.get_schedule(target) if target else None
        every = draft.every or (current.cadence.every if current else None)
        weekday = draft.weekday if draft.weekday is not None else (
            current.cadence.weekday if current else None
        )
        return every == "week" and weekday is None

    async def after_schedule(state: TeamState) -> str:
        if not state.get("schedule_draft"):
            return END
        return "schedule_day" if await needs_day(state) else "schedule_save"

    async def schedule_day(state: TeamState) -> dict:
        ask = Ask(
            team_id=state["team_id"],
            persona=template_of(state).lead.persona,
            question="Which day?",
            quick_replies=WEEKDAYS[:5],
        )
        answer = str(interrupt(ask.model_dump(mode="json")))
        draft = {**state["schedule_draft"], "weekday": weekday_of(answer)}
        return {"schedule_draft": draft}

    async def schedule_save(state: TeamState) -> dict:
        draft = ScheduleDraft.model_validate(state["schedule_draft"])
        target = state.get("schedule_target")
        current = await deps.store.get_schedule(target) if target else None
        profile = await deps.store.get_profile(state["business_id"])
        zone = (profile.extra.get("timezone") if profile else None) or None
        cadence = merge_cadence(draft, current.cadence if current else None, zone)
        request = (draft.request or "").strip() or (
            current.request if current else state["request"]
        )
        title = (draft.title or "").strip() or (current.title if current else request[:40])
        saved = Schedule(
            schedule_id=current.schedule_id if current else new_id(),
            business_id=state["business_id"],
            team_id=state["team_id"],
            title=title,
            request=request,
            cadence=cadence,
            active=True,
            next_run_at=current.next_run_at if current else None,
            last_run_at=current.last_run_at if current else None,
            created_at=current.created_at if current else deps.clock(),
        )
        await deps.store.save_schedule(saved)
        lead = template_of(state).lead.persona
        emit(ScheduleSaved(team_id=state["team_id"], persona=lead, schedule=saved))
        when = cadence_words(cadence)
        if current is None:
            lead_says(
                state, f"Done. {when}: {request}. Tap Run now to see the first one today."
            )
        else:
            lead_says(state, f"Updated “{title}”: {when}, {request}")
        return {"schedule_draft": None, "schedule_target": None}

    async def channels(state: TeamState) -> dict:
        """
        A campaign that doesn't say where it goes out: ask, then route again with the answer.
        """
        template = template_of(state)
        spec = template.channel_question
        lead = template.lead.persona
        ask = Ask(
            team_id=state["team_id"],
            persona=lead,
            question=spec.question,
            quick_replies=spec.quick_replies,
        )
        answer = str(interrupt(ask.model_dump(mode="json")))
        return {
            "request": f"{state['request']}\n\n{lead.name}: {spec.question}\nFounder: {answer}",
            "ask_channels": False,
        }

    async def reply(state: TeamState) -> dict:
        template = template_of(state)
        context = await build_context(deps, state["business_id"], state["team_id"], None)
        messages = reply_messages(
            template, state["request"], context, no_match=state.get("reply_reason") == "no_match"
        )
        completion = await deps.llm.complete(
            deps.settings.model_lead, messages, reasoning=state.get("reasoning", False)
        )
        emit(
            Say(
                team_id=state["team_id"],
                persona=template.lead.persona,
                text=(completion.text or "").strip() or "Got it.",
            )
        )
        return {"tokens_used": state.get("tokens_used", 0) + completion.tokens}

    async def route(state: TeamState) -> dict:
        """
        Re-routes after the founder answered a clarifying question (no feedback check: the
        original message was already triaged). An answer that names no deliverable, like "yes",
        keeps the work already planned.
        """
        template = template_of(state)
        decisions = await decide(
            deps, route_questions(template), triage_state(template, state["request"])
        )
        return {
            "routed": routed_from(template, decisions) or state.get("routed", []),
            "reply_reason": "no_match",
            "tokens_used": state.get("tokens_used", 0) + decisions.tokens,
        }

    async def plan(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        status = f"{lead.name} is planning the work"
        emit(Progress(team_id=state["team_id"], persona=lead, status=status))
        context = await build_context(deps, state["business_id"], state["team_id"], None)
        schedule_id = state.get("schedule_id")
        earlier = (state.get("schedule_history") or {}).get(schedule_id or "", [])
        if earlier:
            runs = "; ".join(earlier)
            context += f"\n\nEarlier runs of this schedule: {runs}. Pick something new."
        tokens = state.get("tokens_used", 0)
        try:
            result = await deps.llm.structured(
                deps.settings.model_lead,
                plan_messages(
                    template, state["routed"], state["request"], context, state.get("history")
                ),
                LeadPlan,
                reasoning=True,
            )
            lead_plan, tokens = result.value, tokens + result.tokens
        except Exception as error:
            log.warning("Lead plan failed, planning from the request: %s", error)
            lead_plan = LeadPlan(tasks=[])
        plans = fix_plan(template, state["routed"], lead_plan, state["request"])
        assumptions = list(state.get("assumptions") or [])

        if state.get("clarified", 0) == 0 and lead_plan.question:
            briefs = "\n".join(f"- {p.task_type}: {p.brief}" for p in plans)
            clarity = await decide(
                deps, {"is_clear": IS_CLEAR}, f"Request:\n{state['request']}\n\nPlan:\n{briefs}"
            )
            tokens += clarity.tokens
            unclear = not clarity["is_clear"].accepts("clear", threshold)
            if unclear and not schedule_id:
                return {"plan_question": lead_plan.question, "tokens_used": tokens}
            if unclear:
                assumptions.append(
                    f"I didn't stop to ask: {lead_plan.question} I went with my best guess; "
                    "tell me if you want it different."
                )

        now = deps.clock()
        tasks, order = {}, []
        media = state.get("media") or {}
        teams = await deps.store.list_teams(state["business_id"])
        ready = {team.template for team in teams if team.onboarded}
        hired = {team.template for team in teams}
        choice, image_tokens = await image_choice(state, plans, ready)
        tokens += image_tokens
        if choice == "no":
            ready = set()
        for item in plans:
            task = Task(
                task_id=new_id(),
                business_id=state["business_id"],
                team_id=state["team_id"],
                task_type=item.task_type,
                title=item.title,
                brief=item.brief,
                status=TaskStatus.PLANNED,
                steps=planned_steps(template.task_types[item.task_type], ready),
                schedule_id=schedule_id,
                created_at=now,
                updated_at=now,
            )
            await deps.store.save_task(task)
            ts = TaskState(task=task, reasoning=state.get("reasoning", False), media=media)
            tasks[task.task_id] = ts.model_dump(mode="json")
            order.append(task.task_id)
        offered = set(state.get("suggested_teams") or [])
        suggest = [
            name
            for item in plans
            for name in template.task_types[item.task_type].with_teams
            if name not in hired and name not in offered and name in deps.templates
        ]
        planned = "\n".join(f"- {p.title}: {p.brief}" for p in plans)
        entry = f"Founder: {state['request']}\nPlanned:\n{planned}"
        history = [*(state.get("history") or []), entry][-MAX_HISTORY:]
        return {
            "plan_question": None,
            "tasks": tasks,
            "order": order,
            "history": history,
            "suggest": list(dict.fromkeys(suggest)),
            "assumptions": assumptions,
            "schedule_history": runs_after(state, [p.title for p in plans]),
            "ask_image": choice == "ask",
            "tokens_used": tokens,
        }

    async def image_choice(
        state: TeamState, plans: list[TaskPlan], ready: set[str]
    ) -> tuple[Literal["yes", "no", "ask"] | None, int]:
        """
        Whether this request comes with another team's image (the Design team's illustrator):
        "yes" or "no" when the request says so, "ask" when it doesn't. None when no planned task
        would get such a step. A scheduled run never asks: it makes an image only when its
        request says so.
        """
        template = template_of(state)
        if not any(set(template.task_types[item.task_type].with_teams) & ready for item in plans):
            return None, 0
        questions = {"mentions_image": MENTIONS_IMAGE, "wants_image": WANTS_IMAGE}
        decisions = await decide(deps, questions, f"Request:\n{state['request']}")
        if decisions["mentions_image"].accepts("yes", threshold):
            wants = decisions["wants_image"].accepts("yes", threshold)
            return ("yes" if wants else "no"), decisions.tokens
        return ("no" if state.get("schedule_id") else "ask"), decisions.tokens

    def illustrator(state: TeamState) -> Persona | None:
        """
        Who would make the request's image: the specialist of the first other team's step.
        """
        for task_id in state.get("order") or []:
            steps = load(state, task_id).task.steps
            index = cross_step(steps)
            if index is None:
                continue
            name, _, specialist_id = steps[index].rpartition(":")
            home = deps.templates.get(name)
            if home is not None and specialist_id in home.specialists:
                return home.specialists[specialist_id].persona
        return None

    async def image(state: TeamState) -> dict:
        """
        The founder didn't say whether to make an image: ask. Anything but a clear yes means no
        image, and the tasks drop the other team's steps.
        """
        template = template_of(state)
        lead = template.lead.persona
        maker = illustrator(state)
        question = IMAGE_QUESTION.format(name=maker.name if maker else "the Design team")
        ask = Ask(
            team_id=state["team_id"], persona=lead, question=question, quick_replies=IMAGE_REPLIES
        )
        answer = str(interrupt(ask.model_dump(mode="json")))
        decisions = await decide(
            deps, {"wants_image": WANTS_IMAGE}, f"{lead.name}: {question}\nFounder: {answer}"
        )
        update: dict = {
            "ask_image": False,
            "tokens_used": state.get("tokens_used", 0) + decisions.tokens,
        }
        if decisions["wants_image"].accepts("yes", threshold):
            return update
        tasks = dict(state["tasks"])
        for task_id in state["order"]:
            ts = load(state, task_id)
            own = [step for step in ts.task.steps if ":" not in step]
            if own != ts.task.steps:
                task = await save(ts.task, steps=own)
                tasks[task_id] = ts.model_copy(update={"task": task}).model_dump(mode="json")
        return {**update, "tasks": tasks}

    def after_plan(state: TeamState) -> str:
        if state.get("plan_question"):
            return "clarify"
        return "image" if state.get("ask_image") else "dispatch"

    async def clarify(state: TeamState) -> dict:
        template = template_of(state)
        ask = Ask(
            team_id=state["team_id"],
            persona=template.lead.persona,
            question=state["plan_question"],
        )
        answer = str(interrupt(ask.model_dump(mode="json")))
        lead = template.lead.persona.name
        return {
            "request": f"{state['request']}\n\n{lead}: {state['plan_question']}\nFounder: {answer}",
            "clarified": state.get("clarified", 0) + 1,
            "plan_question": None,
        }

    async def dispatch(state: TeamState) -> dict:
        template = template_of(state)
        for task_id in state.get("order", []):
            ts = load(state, task_id)
            if ts.phase == "done":
                continue
            if state.get("tokens_used", 0) >= template.limits.token_budget:
                return await over_budget(state, ts)
            steps = {"work": "specialist", "check": "check", "gate": "gate"}
            return {"current": task_id, "next": steps[ts.phase]}
        return {"current": None, "next": "report"}

    def has_draft(ts: TaskState) -> bool:
        return ts.task.steps[last_own_step(ts.task.steps)] in ts.outputs

    async def over_budget(state: TeamState, ts: TaskState) -> dict:
        """
        Out of budget: a finished draft still goes to the founder, just without more checks or
        rewrites (approving needs no tokens). Without a draft the task fails.
        """
        task_id = ts.task.task_id
        if ts.phase == "gate":
            return {"current": task_id, "next": "gate"}
        if not has_draft(ts):
            message = f"I ran out of budget before finishing “{ts.task.title}”."
            ts = await fail(state, ts, message)
            return {"tasks": put(state, ts), "current": task_id, "next": "dispatch"}
        lead = template_of(state).lead.persona
        emit(
            Say(
                team_id=state["team_id"],
                task_id=task_id,
                persona=lead,
                text=(
                    f"I stopped polishing “{ts.task.title}” to stay within budget. Here's the "
                    "best draft so far."
                ),
            )
        )
        confidence = ts.check_confidence or 0.0
        ts = ts.model_copy(update={"phase": "gate", "check_confidence": confidence})
        return {"tasks": put(state, ts), "current": task_id, "next": "gate"}

    async def resolve_step(state: TeamState, task: Task) -> StepRun | None:
        """
        Who runs the task's current step, with whose memory. "design:illustrator" runs Design's
        specialist with the Design team's lessons; None if that team isn't hired and ready.
        """
        template = template_of(state)
        step_id = task.steps[task.current_step]
        if ":" not in step_id:
            last = task.current_step == last_own_step(task.steps)
            return StepRun(
                spec=template.specialists[step_id],
                output=template.task_types[task.task_type].output if last else "notes",
                team_id=state["team_id"],
                task_type=task.task_type,
                max_steps=template.limits.max_steps_per_specialist,
            )
        name, _, specialist_id = step_id.partition(":")
        home = deps.templates.get(name)
        teams = await deps.store.list_teams(state["business_id"])
        team = next((t for t in teams if t.template == name and t.onboarded), None)
        if home is None or team is None or specialist_id not in home.specialists:
            return None
        task_type = home_task_type(home, specialist_id)
        return StepRun(
            spec=home.specialists[specialist_id],
            output=home.task_types[task_type].output if task_type else "notes",
            team_id=team.team_id,
            task_type=task_type,
            max_steps=home.limits.max_steps_per_specialist,
        )

    def images_made(state: TeamState) -> int:
        return sum(
            1
            for data in (state.get("tasks") or {}).values()
            for file in TaskState.model_validate(data).media.values()
            if file.source == "generated"
        )

    async def specialist(state: TeamState) -> dict:
        template = template_of(state)
        ts = load(state, state["current"])
        enter_task(ts.task.task_id)
        task = ts.task
        step = task.current_step
        step_id = task.steps[step]
        if ":" in step_id and ts.rendered and not ts.redraw:
            task = await save(task, current_step=step + 1)
            phase = "check" if step + 1 >= len(task.steps) else "work"
            return {"tasks": put(state, ts.model_copy(update={"task": task, "phase": phase}))}
        run = await resolve_step(state, task)
        if run is None:
            log.warning("Skipping step %s: its team isn't hired and ready", step_id)
            task = await save(task, current_step=step + 1)
            phase = "check" if step + 1 >= len(task.steps) else "work"
            return {"tasks": put(state, ts.model_copy(update={"task": task, "phase": phase}))}
        spec = run.spec
        can_attach = "images" in OUTPUTS[run.output].schema.model_fields
        prior = {key: value for key, value in ts.outputs.items() if key != step_id}
        context = await build_context(
            deps,
            state["business_id"],
            run.team_id,
            run.task_type,
            prior_outputs=prior,
            feedback=ts.feedback,
            photos=list(ts.media.values()) if can_attach else None,
        )
        image_limit = max(0, template.limits.max_images_per_request - images_made(state))
        task = await save(task, status=TaskStatus.IN_PROGRESS)
        ts = ts.model_copy(update={"task": task})
        try:
            final = await run_specialist(
                specialist_graph,
                {
                    "business_id": state["business_id"],
                    "team_id": state["team_id"],
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "specialist_id": step_id,
                    "persona": spec.persona,
                    "tool_names": list(spec.tools),
                    "instructions": spec.instructions,
                    "output": run.output,
                    "brief": task.brief,
                    "context": context,
                    "max_steps": run.max_steps,
                    "reasoning": ts.reasoning,
                    "memory_team_id": run.team_id,
                    "memory_task_type": run.task_type,
                    "image_limit": min(MAX_IMAGE_CALLS, image_limit),
                },
            )
        except Exception:
            log.exception("Specialist %s failed", step_id)
            message = f"{spec.persona.name} couldn't finish “{task.title}”. Try asking again."
            return {"tasks": put(state, await fail(state, ts, message))}
        used = final.get("tokens", 0)
        task = await save(task, current_step=step + 1, tokens_used=task.tokens_used + used)
        made = {file.file_id: file for file in final.get("files") or []}
        ts = ts.model_copy(
            update={
                "task": task,
                "outputs": {**ts.outputs, step_id: final["result"]},
                "phase": "check" if step + 1 >= len(task.steps) else "work",
                "media": {**ts.media, **made},
                "prompts": {**ts.prompts, **(final.get("prompts") or {})},
                "aspects": {**ts.aspects, **(final.get("aspects") or {})},
            }
        )
        return {"tasks": put(state, ts), "tokens_used": state.get("tokens_used", 0) + used}

    def final_output(template: Template, ts: TaskState) -> BaseModel:
        """
        The task's own output (its last own step), with the images of any other team's steps
        added to it: a post's text with Design's image.
        """
        output_type = OUTPUTS[template.task_types[ts.task.task_type].output]
        result = dict(ts.outputs[ts.task.steps[last_own_step(ts.task.steps)]])
        extra = [
            file_id
            for step_id in ts.task.steps
            if ":" in step_id
            for file_id in (ts.outputs.get(step_id) or {}).get("images", [])
        ]
        if extra and "images" in output_type.schema.model_fields:
            images = list(dict.fromkeys([*result.get("images", []), *extra]))
            result["images"] = images[:MAX_IMAGES]
        return output_type.schema.model_validate(result)

    async def rerun_from(ts: TaskState, feedback: list[str]) -> tuple[int, int]:
        """
        Which step a revision starts at, and the tokens it took: with another team's step in
        the task, one decide() question says whether the feedback is only about the image.
        """
        steps = ts.task.steps
        cross = cross_step(steps)
        if cross is None or (ts.rendered and not ts.redraw):
            return last_own_step(steps), 0
        state = "Feedback on the draft:\n" + "\n".join(f"- {item}" for item in feedback)
        decisions = await decide(deps, {"about_image": ABOUT_IMAGE}, state)
        if decisions["about_image"].accepts("yes", threshold):
            return cross, decisions.tokens
        return last_own_step(steps), decisions.tokens

    async def check(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        ts = load(state, state["current"])
        enter_task(ts.task.task_id)
        task = ts.task
        emit(
            Progress(
                team_id=state["team_id"],
                task_id=task.task_id,
                persona=lead,
                status=f"{lead.name} is reviewing “{task.title}”",
            )
        )
        lessons = await deps.store.list_lessons(
            state["business_id"], state["team_id"], task.task_type
        )
        profile = await deps.store.get_profile(state["business_id"])
        output = final_output(template, ts)
        asked = state.get("request") or task.brief
        result = await run_check(
            deps,
            template,
            task.task_type,
            output,
            lessons,
            profile,
            asked,
            media=ts.media,
            prompts=ts.prompts,
        )
        tokens = state.get("tokens_used", 0) + result.tokens
        if result.passed or ts.checks >= MAX_CHECKS:
            task = await save(task, tokens_used=task.tokens_used + result.tokens)
            ts = ts.model_copy(
                update={"task": task, "phase": "gate", "check_confidence": result.confidence}
            )
        else:
            step, routing = await rerun_from(ts, result.feedback)
            tokens += routing
            task = await save(
                task,
                current_step=step,
                tokens_used=task.tokens_used + result.tokens + routing,
            )
            ts = ts.model_copy(
                update={
                    "task": task,
                    "phase": "work",
                    "feedback": result.feedback,
                    "checks": ts.checks + 1,
                }
            )
        return {"tasks": put(state, ts), "tokens_used": tokens}

    def drawer(state: TeamState, ts: TaskState, file_id: str) -> Persona:
        """
        The specialist whose step planned this image, for the "drawing" progress line.
        """
        template = template_of(state)
        for step_id in ts.task.steps:
            if file_id not in (ts.outputs.get(step_id) or {}).get("images", []):
                continue
            name, _, specialist_id = step_id.rpartition(":")
            home = deps.templates.get(name) if name else template
            if home is not None and specialist_id in home.specialists:
                return home.specialists[specialist_id].persona
        return template.lead.persona

    async def render(state: TeamState, ts: TaskState) -> TaskState:
        """
        Makes the draft's planned images, once: here, after the lead's review passed, so a
        revision never pays for an image that's thrown away. A planned image that can't be
        made is dropped; a draft that needs one then fails at the gate.
        """
        plans = {file_id: file for file_id, file in ts.media.items() if is_plan(file)}
        if not plans:
            return ts
        enter_task(ts.task.task_id)
        wanted = getattr(final_output(template_of(state), ts), "images", [])
        made: dict[str, FileRef] = {}
        for file_id in [f for f in wanted if f in plans]:
            plan = plans[file_id]
            persona = drawer(state, ts, file_id)
            subject = plan.alt_text or "the image"
            emit(
                Progress(
                    team_id=state["team_id"],
                    task_id=ts.task.task_id,
                    persona=persona,
                    status=f"{persona.name} is drawing: {subject}",
                )
            )
            prompt = ts.prompts.get(file_id) or subject
            try:
                image = await deps.images.generate(
                    deps.settings.model_image, prompt, ts.aspects.get(file_id, "1:1")
                )
                file = await deps.tools.save_file(
                    state["business_id"], image.data, image.mime_type, alt_text=plan.alt_text
                )
            except (ImageError, AttributeError) as error:
                log.warning("Couldn't make planned image %s: %s", file_id, error)
                file = None
            if file is not None:
                made[file_id] = file

        def swap(ids: list[str]) -> list[str]:
            return [made[i].file_id if i in made else i for i in ids if i not in plans or i in made]

        outputs = {
            step_id: {**output, "images": swap(output["images"])} if "images" in output else output
            for step_id, output in ts.outputs.items()
        }
        media = {k: v for k, v in ts.media.items() if k not in plans}
        media.update({file.file_id: file for file in made.values()})
        prompts = {made[k].file_id if k in made else k: v for k, v in ts.prompts.items()}
        return ts.model_copy(
            update={
                "outputs": outputs,
                "media": media,
                "prompts": {k: v for k, v in prompts.items() if k not in plans},
                "aspects": {},
                "rendered": True,
                "redraw": False,
            }
        )

    async def gate_node(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        ts = await render(state, load(state, state["current"]))
        task = ts.task
        task_spec = template.task_types[task.task_type]
        output_type = OUTPUTS[task_spec.output]
        output = final_output(template, ts)
        preview = output_type.preview(output)
        images = output_type.images(output, ts.media)
        if len(images) < output_type.min_images:
            message = f"I couldn't make the image for “{task.title}”. Try asking again."
            return {"tasks": put(state, await fail(state, ts, message))}
        try:
            planned = output_type.to_planned_action(output, task_spec.action, ts.media)
        except (ValidationError, ValueError):
            message = f"I couldn't get “{task.title}” into a shape I can send. Try asking again."
            return {"tasks": put(state, await fail(state, ts, message))}

        trust = await deps.store.get_trust(state["team_id"], task.task_type)
        if trust is None:
            trust = Trust(
                team_id=state["team_id"],
                task_type=task.task_type,
                level=task_spec.start_level,
                updated_at=deps.clock(),
            )
        mode = gate(trust.level)
        confidence = ts.check_confidence or 0.0
        if mode == "approval":
            if trust.level == AutonomyLevel.DRAFT_ONLY:
                planned = None
            media = images if planned is None else []
            approval = Approval(
                approval_id=new_id(),
                business_id=state["business_id"],
                team_id=state["team_id"],
                task_id=task.task_id,
                task_type=task.task_type,
                preview=preview,
                planned_action=planned,
                media=media,
                check_confidence=confidence,
                created_at=deps.clock(),
            )
            await deps.store.save_approval(approval)
            task = await save(task, status=TaskStatus.WAITING_APPROVAL)
            emit(
                NeedsApproval(
                    approval_id=approval.approval_id,
                    team_id=state["team_id"],
                    task_id=task.task_id,
                    task_type=task.task_type,
                    persona=lead,
                    preview=preview,
                    planned_action=planned,
                    media=media,
                    check_confidence=confidence,
                )
            )
        elif planned is None:
            emit(
                Say(
                    team_id=state["team_id"],
                    task_id=task.task_id,
                    persona=lead,
                    text=f"{task.title}:\n\n{preview}",
                    media=images,
                )
            )
            task = await save(task, status=TaskStatus.DONE)
        else:
            outcome = await execute_action(
                deps, task, planned, approval_id=None, autonomous=mode == "act_autonomous"
            )
            emit(outcome)
            done = isinstance(outcome, ActionDone)
            task = await save(task, status=TaskStatus.DONE if done else TaskStatus.FAILED)
        ts = ts.model_copy(update={"task": task, "phase": "done"})
        return {"tasks": put(state, ts)}

    def remember(state: TeamState) -> dict[str, dict]:
        """
        Keeps this request's tasks (outputs, photos, images) in the checkpoint, so a later
        reject-and-revise can pick a task up where it stopped.
        """
        past = dict(state.get("past_tasks") or {})
        for task_id in state.get("order", []):
            past.pop(task_id, None)
            past[task_id] = state["tasks"][task_id]
        return dict(list(past.items())[-MAX_PAST_TASKS:])

    async def report(state: TeamState) -> dict:
        tasks = [load(state, task_id).task for task_id in state.get("order", [])]
        text = report_text(tasks)
        if not text:
            return {"past_tasks": remember(state)}
        suggest = [deps.templates[name] for name in state.get("suggest") or []]
        lines = [*(state.get("assumptions") or [])]
        lines += [
            f"Want {other.description[0].lower()}{other.description[1:]}? Hire the "
            f"{other.display_name} team with /hire {other.name}."
            for other in suggest
        ]
        text = "\n\n".join([text, *lines])
        emit(Say(team_id=state["team_id"], persona=template_of(state).lead.persona, text=text))
        offered = [*(state.get("suggested_teams") or []), *(other.name for other in suggest)]
        return {"suggested_teams": offered, "suggest": [], "past_tasks": remember(state)}

    def runs_after(state: TeamState, titles: list[str]) -> dict[str, list[str]]:
        """
        The last few task titles per schedule, so the next run picks a new topic.
        """
        history = dict(state.get("schedule_history") or {})
        schedule_id = state.get("schedule_id")
        if schedule_id:
            history[schedule_id] = [*history.get(schedule_id, []), *titles][-MAX_SCHEDULE_RUNS:]
        return history

    graph = StateGraph(TeamState)
    for name, node in [
        ("entry", entry),
        ("onboard_intro", onboard_intro),
        ("onboard", onboard),
        ("onboard_done", onboard_done),
        ("triage", triage),
        ("reply", reply),
        ("route", route),
        ("plan", plan),
        ("clarify", clarify),
        ("channels", channels),
        ("schedule", schedule),
        ("change_schedule", change_schedule),
        ("schedule_day", schedule_day),
        ("schedule_save", schedule_save),
        ("image", image),
        ("dispatch", dispatch),
        ("specialist", specialist),
        ("check", check),
        ("gate", gate_node),
        ("report", report),
    ]:
        graph.add_node(name, node)

    graph.add_edge(START, "entry")
    graph.add_conditional_edges(
        "entry", after_entry, ["dispatch", "onboard_intro", "onboard_done", "triage"]
    )
    graph.add_edge("onboard_intro", "onboard")
    graph.add_conditional_edges("onboard", after_onboard, ["onboard", "onboard_done"])
    graph.add_edge("onboard_done", END)
    graph.add_conditional_edges(
        "triage", after_triage, ["schedule", "change_schedule", "channels", "plan", "reply"]
    )
    for node in ("schedule", "change_schedule"):
        graph.add_conditional_edges(node, after_schedule, ["schedule_day", "schedule_save", END])
    graph.add_edge("schedule_day", "schedule_save")
    graph.add_edge("schedule_save", END)
    graph.add_edge("channels", "route")
    graph.add_edge("reply", END)
    graph.add_conditional_edges(
        "route", lambda s: "plan" if s["routed"] else "reply", ["plan", "reply"]
    )
    graph.add_conditional_edges("plan", after_plan, ["clarify", "image", "dispatch"])
    graph.add_edge("image", "dispatch")
    graph.add_edge("clarify", "route")
    graph.add_conditional_edges(
        "dispatch",
        lambda s: s["next"],
        ["dispatch", "specialist", "check", "gate", "report"],
    )
    graph.add_edge("specialist", "dispatch")
    graph.add_edge("check", "dispatch")
    graph.add_edge("gate", "dispatch")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)

@dataclass(frozen=True)
class StepRun:
    """
    One step as it runs: the specialist, its output type, and whose memory (team and task type)
    it works with.
    """

    spec: SpecialistSpec
    output: str
    team_id: str
    task_type: str | None
    max_steps: int

def weekday_of(answer: str) -> int:
    """
    The weekday a founder's answer names ("Tuesday", "tue"); Monday if it names none.
    """
    text = answer.strip().lower()
    for index, name in enumerate(WEEKDAYS):
        if name.lower()[:3] in text:
            return index
    return 0

def valid_zone(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name

def merge_cadence(
    draft: ScheduleDraft, current: Cadence | None, profile_zone: str | None
) -> Cadence:
    """
    Code fills the cadence: what the founder said, else the schedule being changed, else the
    defaults (daily at 9:00). The timezone is the one they named, else the schedule's, else the
    profile's, else the default.
    """

    def pick(field: str, default):
        value = getattr(draft, field)
        if value is not None:
            return value
        return getattr(current, field) if current is not None else default

    fallback = "week" if draft.weekday is not None else "day"
    every = draft.every or (current.every if current else fallback)
    weekday = pick("weekday", 0) if every == "week" else None
    day = pick("day", 1) if every == "month" else None
    timezone = (
        valid_zone(draft.timezone)
        or (current.timezone if current else None)
        or valid_zone(profile_zone)
        or Cadence.model_fields["timezone"].default
    )
    return Cadence(
        every=every,
        weekday=_clamp(weekday or 0, 0, 6) if every == "week" else None,
        day=_clamp(day or 1, 1, 28) if every == "month" else None,
        hour=_clamp(pick("hour", 9), 0, 23),
        minute=_clamp(pick("minute", 0), 0, 59),
        timezone=timezone,
    )

def _clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)

def last_own_step(steps: list[str]) -> int:
    """
    The index of the task's last step run by its own team: the one that writes the draft.
    """
    own = [index for index, step in enumerate(steps) if ":" not in step]
    return own[-1] if own else len(steps) - 1

def cross_step(steps: list[str]) -> int | None:
    """
    The index of the first step another team runs ("design:illustrator"), if any.
    """
    return next((index for index, step in enumerate(steps) if ":" in step), None)

def planned_steps(spec: TaskTypeSpec, ready: set[str]) -> list[str]:
    """
    The task type's fixed steps, plus the `with_teams` steps of teams this business has hired
    and onboarded.
    """
    extra = [
        f"{name}:{specialist_id}"
        for name, specialist_ids in spec.with_teams.items()
        if name in ready
        for specialist_id in specialist_ids
    ]
    return [*spec.steps, *extra]

def home_task_type(template: Template, specialist_id: str) -> str | None:
    """
    The task type a specialist works on in its own template, for its lessons and examples.
    """
    return next(
        (name for name, spec in template.task_types.items() if specialist_id in spec.steps),
        None,
    )

def fix_plan(template: Template, routed: list[str], plan: LeadPlan, request: str) -> list[TaskPlan]:
    """
    Code fixes the plan's structure: only routed task types, one task each, in the lead's order,
    then any routed type the lead forgot; empty titles and briefs get sensible defaults.
    """
    chosen: dict[str, TaskPlan] = {}
    for item in plan.tasks:
        if item.task_type in routed and item.task_type not in chosen:
            chosen[item.task_type] = item
    for task_type in routed:
        chosen.setdefault(task_type, TaskPlan(task_type=task_type, title="", brief=""))
    fixed = []
    for task_type, item in chosen.items():
        spec = template.task_types[task_type]
        fixed.append(
            TaskPlan(
                task_type=task_type,
                title=item.title.strip() or spec.description,
                brief=item.brief.strip() or request,
            )
        )
    return fixed[: template.limits.max_tasks_per_request]
