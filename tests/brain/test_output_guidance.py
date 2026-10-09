import pytest

from brain.graphs.specialist import build_specialist_graph
from brain.templates.registries import OUTPUTS, POST_LIMIT
from contract import Persona

def test_every_output_type_has_guidance():
    assert all(output_type.guidance for output_type in OUTPUTS.values())

def test_post_guidance_states_the_bluesky_limit():
    assert str(POST_LIMIT) in OUTPUTS["social_post"].guidance

@pytest.mark.parametrize("output", ["social_post", "email", "report", "notes"])
async def test_specialist_prompt_includes_the_output_guidance(deps, llm, output):
    schema = OUTPUTS[output].schema.__name__
    llm.structured_responses[schema] = {
        "social_post": {"text": "Hi"},
        "email": {"to": "a@b.co", "subject": "Hi", "body": "Hi"},
        "report": {"summary": "Hi", "sources": ["https://a.co"]},
        "notes": {"notes": "Hi", "sources": []},
    }[output]

    graph = build_specialist_graph(deps)
    await graph.ainvoke(
        {
            "business_id": "b1",
            "team_id": "t1",
            "task_id": "k1",
            "task_type": "social_post",
            "specialist_id": "writer",
            "persona": Persona(name="Leo", role="Writer"),
            "tool_names": [],
            "output": output,
            "brief": "Announce Friday",
            "context": "",
            "max_steps": 2,
        }
    )

    assert OUTPUTS[output].guidance in str(llm.calls[0].messages)
