import pytest
from pydantic import BaseModel

from brain.deps import Deps, Settings
from brain.fakes import FakeTools, InMemoryStore
from brain.helpers.decide import Question, decide
from brain.helpers.jev import OpenRouterJev
from brain.helpers.llm import OpenRouterLLM
from brain.templates.loader import load_templates

pytestmark = pytest.mark.live

class Capital(BaseModel):
    city: str

@pytest.fixture
def live_deps():
    settings = Settings.from_env()
    if settings.openrouter_api_key is None:
        pytest.skip("OPENROUTER_API_KEY is not set")
    return Deps(
        store=InMemoryStore(),
        tools=FakeTools(),
        llm=OpenRouterLLM(settings),
        jev=OpenRouterJev(settings),
        settings=settings,
        templates=load_templates(),
    )

async def test_live_jev_decide(live_deps):
    question = Question.yes_no(
        "Does the message give feedback or a preference about how the team works?",
        yes="It gives feedback or a preference",
        no="No feedback",
    )
    decisions = await decide(
        live_deps,
        {"has_feedback": question},
        "Stop using hashtags. Also post about our Friday launch.",
    )
    assert decisions["has_feedback"].choice == "yes"
    assert decisions["has_feedback"].source == "jev"
    assert decisions.tokens > 0

async def test_live_structured_output(live_deps):
    messages = [{"role": "user", "content": "What is the capital of France? Reply as JSON."}]
    result = await live_deps.llm.structured(live_deps.settings.model_lead, messages, Capital)
    assert "Paris" in result.value.city
    assert result.tokens > 0
