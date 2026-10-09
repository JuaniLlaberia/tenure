from datetime import UTC, datetime

import pytest

from brain.brain import create_brain
from brain.deps import Settings
from brain.fakes import FakeTools, InMemoryStore
from contract import (
    ActionDone,
    ApprovalDecision,
    BusinessProfile,
    Error,
    IncomingMessage,
    NeedsApproval,
    OnboardingComplete,
    TeamHired,
)

pytestmark = pytest.mark.live

def of(events, kind):
    return [e for e in events if isinstance(e, kind)]

async def test_live_demo_script(collect):
    settings = Settings.from_env()
    if settings.openrouter_api_key is None:
        pytest.skip("OPENROUTER_API_KEY is not set")
    store = InMemoryStore()
    await store.save_profile(
        BusinessProfile(
            business_id="b1",
            name="Juan's Studio",
            what_you_sell="Brand identity design for independent cafés",
            customers="Independent café owners in the Bay Area",
            tone="warm, plain-spoken, no jargon",
        )
    )
    brain = create_brain(store, FakeTools(), settings)

    def say(team_id, text, n):
        return IncomingMessage(
            business_id="b1",
            team_id=team_id,
            text=text,
            message_id=f"m{n}",
            sent_at=datetime.now(UTC),
        )

    hired = await collect(brain.hire_team("b1", "marketing"))
    team_id = of(hired, TeamHired)[0].team_id
    answers = ["Bluesky only", "We open our studio this Friday, Oct 10", "hello@juans.studio"]
    for n, answer in enumerate(answers, start=1):
        events = await collect(brain.handle_message(say(team_id, answer, n)))
    assert of(events, OnboardingComplete)

    request = say(team_id, "Write a Bluesky post announcing our studio opening this Friday", 9)
    work = await collect(brain.handle_message(request))
    assert not of(work, Error)
    draft = of(work, NeedsApproval)[0]
    assert draft.task_type == "social_post"
    assert len(draft.preview) <= 300

    edit = ApprovalDecision(
        business_id="b1",
        approval_id=draft.approval_id,
        decision="edit",
        edited_text="Our studio opens this Friday, Oct 10. Café owners, come say hi. — Juan",
    )
    done = await collect(brain.resolve_approval(edit))
    assert of(done, ActionDone)
