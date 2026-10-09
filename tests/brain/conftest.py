from datetime import UTC, datetime, timedelta

import pytest

from brain.brain import TenureBrain
from brain.common import new_id
from brain.deps import Deps, Settings
from brain.fakes import FakeJev, FakeLLM, FakeTools, InMemoryStore
from brain.templates.loader import load_templates
from contract import (
    Approval,
    IncomingMessage,
    PostSocial,
    SendEmail,
    Task,
    TaskStatus,
    Team,
    Trust,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

POST = PostSocial(text="We launch Friday!")
EMAIL = SendEmail(to="list@b.co", subject="Launch", body="We launch Friday.")

class Clock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)

@pytest.fixture
def settings():
    return Settings()

@pytest.fixture
def store():
    return InMemoryStore()

@pytest.fixture
def tools():
    return FakeTools()

@pytest.fixture
def llm():
    return FakeLLM()

@pytest.fixture
def jev():
    return FakeJev()

@pytest.fixture
def clock():
    return Clock()

@pytest.fixture
def deps(store, tools, llm, jev, settings, clock):
    return Deps(
        store=store,
        tools=tools,
        llm=llm,
        jev=jev,
        settings=settings,
        templates=load_templates(),
        clock=clock,
    )

@pytest.fixture
def collect():
    async def collect(stream):
        return [event async for event in stream]

    return collect

@pytest.fixture
def make_team(deps):
    async def make(
        business_id="b1", template="marketing", levels=None, streaks=None, promote_after=None
    ) -> Team:
        spec = deps.templates[template]
        team = Team(
            team_id=new_id(),
            business_id=business_id,
            template=template,
            display_name=spec.display_name,
            onboarded=True,
            created_at=deps.clock(),
        )
        await deps.store.save_team(team)
        for task_type, task_spec in spec.task_types.items():
            await deps.store.set_trust(
                Trust(
                    team_id=team.team_id,
                    task_type=task_type,
                    level=(levels or {}).get(task_type, task_spec.start_level),
                    approval_streak=(streaks or {}).get(task_type, 0),
                    promote_after=(promote_after or {}).get(task_type, 5),
                    updated_at=deps.clock(),
                )
            )
        return team

    return make

TITLES = {
    "social_post": "Launch post",
    "newsletter": "Launch newsletter",
    "competitor_check": "Competitor check",
}

OUTPUTS = {
    "SocialPostOutput": {"text": "We launch Friday!"},
    "EmailOutput": {"to": "list@b.co", "subject": "We launch Friday", "body": "Join us Friday."},
    "NotesOutput": {"notes": "Rivals post on Mondays", "sources": ["https://a.co/rivals"]},
    "ReportOutput": {"summary": "Rivals cut prices this week", "sources": ["https://a.co/rivals"]},
    "CriticReport": {"issues": ["Mention the date"]},
}

@pytest.fixture
def brain(deps):
    return TenureBrain(deps)

@pytest.fixture
def script(jev, llm):
    """
    Scripts Jev and the LLM for a normal request: routes `task_types`, plans one task per type,
    and every check passes unless told otherwise. `work` below 0.5 means the message asks for
    nothing, so no task type is routed.
    """

    def script(
        task_types=("social_post",),
        feedback=0.1,
        work=0.9,
        clear=0.9,
        passes=0.9,
        plan=None,
        question=None,
    ):
        jev.answers.update({"has_feedback": feedback, "is_clear": clear, "passes_check": passes})
        routed = task_types if work >= 0.5 else ()
        for task_type in TITLES:
            jev.answers[f"needs_{task_type}"] = 0.9 if task_type in routed else 0.1
        tasks = [
            {"task_type": t, "title": TITLES[t], "brief": f"Brief for {t}"} for t in task_types
        ]
        llm.structured_responses.update(OUTPUTS)
        llm.structured_responses["LeadPlan"] = plan or {"tasks": tasks, "question": question}

    return script

@pytest.fixture
def message():
    def message(team, text, message_id="m1"):
        return IncomingMessage(
            business_id=team.business_id,
            team_id=team.team_id,
            text=text,
            message_id=message_id,
            sent_at=NOW,
        )

    return message

def preview_for(planned_action) -> str:
    if isinstance(planned_action, PostSocial):
        return planned_action.text
    if isinstance(planned_action, SendEmail):
        action = planned_action
        return f"To: {action.to}\nSubject: {action.subject}\n\n{action.body}"
    return "Competitors were quiet this week."

@pytest.fixture
def make_approval(deps):
    async def make(
        team,
        task_type="social_post",
        planned_action=POST,
        confidence=0.9,
        revisions=0,
        status="pending",
    ) -> Approval:
        now = deps.clock()
        steps = deps.templates[team.template].task_types[task_type].steps
        task = Task(
            task_id=new_id(),
            business_id=team.business_id,
            team_id=team.team_id,
            task_type=task_type,
            title="Launch post",
            brief="Post about Friday",
            status=TaskStatus.WAITING_APPROVAL if status == "pending" else TaskStatus.DONE,
            steps=steps,
            current_step=len(steps),
            revisions=revisions,
            created_at=now,
            updated_at=now,
        )
        await deps.store.save_task(task)
        approval = Approval(
            approval_id=new_id(),
            business_id=team.business_id,
            team_id=team.team_id,
            task_id=task.task_id,
            task_type=task_type,
            preview=preview_for(planned_action),
            planned_action=planned_action,
            check_confidence=confidence,
            status=status,
            created_at=now,
            resolved_at=None if status == "pending" else now,
        )
        await deps.store.save_approval(approval)
        return approval

    return make
