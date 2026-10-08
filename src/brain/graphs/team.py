import logging
from collections.abc import AsyncIterator, Callable
from typing import Literal, TypedDict

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
from brain.flows.approvals import MAX_REVISIONS
from brain.flows.autonomy import gate
from brain.graphs.specialist import build_specialist_graph, run_specialist
from brain.helpers.decide import decide
from brain.prompts.lead import (
    HAS_FEEDBACK,
    IS_CLEAR,
    LeadPlan,
    TaskPlan,
    needs_question,
    plan_messages,
    reply_messages,
    report_text,
    triage_state,
)
from brain.templates.models import Template
from brain.templates.registries import OUTPUTS
from contract import (
    ActionDone,
    Approval,
    Ask,
    AutonomyLevel,
    Error,
    Event,
    Lesson,
    NeedsApproval,
    OnboardingComplete,
    Progress,
    Say,
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

class TeamState(TypedDict, total=False):
    business_id: str
    team_id: str
    template: str
    request: str
    message_id: str | None
    revise: str | None
    feedback: str | None
    onboarded: bool
    reply_reason: str | None
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

def new_request(team: Team, text: str, message_id: str | None) -> TeamState:
    """
    The input for a new run on a team's thread. Resets everything per request; keeps the
    onboarding answers already in the checkpoint.
    """
    return {
        "business_id": team.business_id,
        "team_id": team.team_id,
        "template": team.template,
        "request": text,
        "message_id": message_id,
        "revise": None,
        "feedback": None,
        "reply_reason": None,
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
        task = await save(
            task,
            revisions=task.revisions + 1,
            current_step=len(task.steps) - 1,
            status=TaskStatus.IN_PROGRESS,
        )
        feedback = [
            f"The founder rejected your last draft:\n{approval.preview}\n"
            f"Their reason: {state.get('feedback') or 'none given'}"
        ]
        return TaskState(task=task, feedback=feedback)

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
        question = template.onboarding[key]
        ask = Ask(team_id=state["team_id"], persona=template.lead.persona, question=question)
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
        questions = {"has_feedback": HAS_FEEDBACK, **route_questions(template)}
        decisions = await decide(deps, questions, triage_state(template, state["request"]))
        if learn is not None and decisions["has_feedback"].accepts("yes", threshold):
            async for event in learn(state, state["request"]):
                emit(event)
        return {
            "routed": routed_from(template, decisions),
            "reply_reason": "chat",
            "tokens_used": state.get("tokens_used", 0) + decisions.tokens,
        }

    async def reply(state: TeamState) -> dict:
        template = template_of(state)
        context = await build_context(deps, state["business_id"], state["team_id"], None)
        messages = reply_messages(
            template, state["request"], context, no_match=state.get("reply_reason") == "no_match"
        )
        completion = await deps.llm.complete(deps.settings.model_lead, messages)
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
        original message was already triaged).
        """
        template = template_of(state)
        decisions = await decide(
            deps, route_questions(template), triage_state(template, state["request"])
        )
        return {
            "routed": routed_from(template, decisions),
            "reply_reason": "no_match",
            "tokens_used": state.get("tokens_used", 0) + decisions.tokens,
        }

    async def plan(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        status = f"{lead.name} is planning the work"
        emit(Progress(team_id=state["team_id"], persona=lead, status=status))
        context = await build_context(deps, state["business_id"], state["team_id"], None)
        tokens = state.get("tokens_used", 0)
        try:
            result = await deps.llm.structured(
                deps.settings.model_lead,
                plan_messages(template, state["routed"], state["request"], context),
                LeadPlan,
            )
            lead_plan, tokens = result.value, tokens + result.tokens
        except Exception as error:
            log.warning("Lead plan failed, planning from the request: %s", error)
            lead_plan = LeadPlan(tasks=[])
        plans = fix_plan(template, state["routed"], lead_plan, state["request"])

        if state.get("clarified", 0) == 0 and lead_plan.question:
            briefs = "\n".join(f"- {p.task_type}: {p.brief}" for p in plans)
            clarity = await decide(
                deps, {"is_clear": IS_CLEAR}, f"Request:\n{state['request']}\n\nPlan:\n{briefs}"
            )
            tokens += clarity.tokens
            if not clarity["is_clear"].accepts("clear", threshold):
                return {"plan_question": lead_plan.question, "tokens_used": tokens}

        now = deps.clock()
        tasks, order = {}, []
        for item in plans:
            task = Task(
                task_id=new_id(),
                business_id=state["business_id"],
                team_id=state["team_id"],
                task_type=item.task_type,
                title=item.title,
                brief=item.brief,
                status=TaskStatus.PLANNED,
                steps=list(template.task_types[item.task_type].steps),
                created_at=now,
                updated_at=now,
            )
            await deps.store.save_task(task)
            tasks[task.task_id] = TaskState(task=task).model_dump(mode="json")
            order.append(task.task_id)
        return {"plan_question": None, "tasks": tasks, "order": order, "tokens_used": tokens}

    def after_plan(state: TeamState) -> str:
        return "clarify" if state.get("plan_question") else "dispatch"

    async def clarify(state: TeamState) -> dict:
        template = template_of(state)
        ask = Ask(
            team_id=state["team_id"],
            persona=template.lead.persona,
            question=state["plan_question"],
        )
        answer = str(interrupt(ask.model_dump(mode="json")))
        return {
            "request": f"{state['request']}\n\nFounder: {answer}",
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
                message = f"I ran out of budget before finishing “{ts.task.title}”."
                ts = await fail(state, ts, message)
                return {"tasks": put(state, ts), "current": task_id, "next": "dispatch"}
            steps = {"work": "specialist", "check": "check", "gate": "gate"}
            return {"current": task_id, "next": steps[ts.phase]}
        return {"current": None, "next": "report"}

    async def specialist(state: TeamState) -> dict:
        template = template_of(state)
        ts = load(state, state["current"])
        task = ts.task
        step = task.current_step
        specialist_id = task.steps[step]
        spec = template.specialists[specialist_id]
        task_spec = template.task_types[task.task_type]
        is_last = step == len(task.steps) - 1
        prior = {key: value for key, value in ts.outputs.items() if key != specialist_id}
        context = await build_context(
            deps,
            state["business_id"],
            state["team_id"],
            task.task_type,
            prior_outputs=prior,
            feedback=ts.feedback,
        )
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
                    "specialist_id": specialist_id,
                    "persona": spec.persona,
                    "tool_names": list(spec.tools),
                    "output": task_spec.output if is_last else "notes",
                    "brief": task.brief,
                    "context": context,
                    "max_steps": template.limits.max_steps_per_specialist,
                },
            )
        except Exception:
            log.exception("Specialist %s failed", specialist_id)
            message = f"{spec.persona.name} couldn't finish “{task.title}”. Try asking again."
            return {"tasks": put(state, await fail(state, ts, message))}
        used = final.get("tokens", 0)
        task = await save(task, current_step=step + 1, tokens_used=task.tokens_used + used)
        ts = ts.model_copy(
            update={
                "task": task,
                "outputs": {**ts.outputs, specialist_id: final["result"]},
                "phase": "check" if step + 1 >= len(task.steps) else "work",
            }
        )
        return {"tasks": put(state, ts), "tokens_used": state.get("tokens_used", 0) + used}

    def final_output(template: Template, ts: TaskState) -> BaseModel:
        output_type = OUTPUTS[template.task_types[ts.task.task_type].output]
        return output_type.schema.model_validate(ts.outputs[ts.task.steps[-1]])

    async def check(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        ts = load(state, state["current"])
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
        result = await run_check(deps, template, task.task_type, output, lessons, profile)
        tokens = state.get("tokens_used", 0) + result.tokens
        if result.passed or task.revisions >= MAX_REVISIONS:
            task = await save(task, tokens_used=task.tokens_used + result.tokens)
            ts = ts.model_copy(
                update={"task": task, "phase": "gate", "check_confidence": result.confidence}
            )
        else:
            task = await save(
                task,
                revisions=task.revisions + 1,
                current_step=len(task.steps) - 1,
                tokens_used=task.tokens_used + result.tokens,
            )
            ts = ts.model_copy(update={"task": task, "phase": "work", "feedback": result.feedback})
        return {"tasks": put(state, ts), "tokens_used": tokens}

    async def gate_node(state: TeamState) -> dict:
        template = template_of(state)
        lead = template.lead.persona
        ts = load(state, state["current"])
        task = ts.task
        task_spec = template.task_types[task.task_type]
        output_type = OUTPUTS[task_spec.output]
        output = final_output(template, ts)
        preview = output_type.preview(output)
        try:
            planned = output_type.to_planned_action(output, task_spec.action)
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
            approval = Approval(
                approval_id=new_id(),
                business_id=state["business_id"],
                team_id=state["team_id"],
                task_id=task.task_id,
                task_type=task.task_type,
                preview=preview,
                planned_action=planned,
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

    async def report(state: TeamState) -> dict:
        tasks = [load(state, task_id).task for task_id in state.get("order", [])]
        text = report_text(tasks)
        if text:
            emit(Say(team_id=state["team_id"], persona=template_of(state).lead.persona, text=text))
        return {}

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
        "triage", lambda s: "plan" if s["routed"] else "reply", ["plan", "reply"]
    )
    graph.add_edge("reply", END)
    graph.add_conditional_edges(
        "route", lambda s: "plan" if s["routed"] else "reply", ["plan", "reply"]
    )
    graph.add_conditional_edges("plan", after_plan, ["clarify", "dispatch"])
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
