import copy

import pytest
import yaml

from brain.templates.loader import (
    TEMPLATES_DIR,
    TemplateError,
    load_template,
    load_templates,
    template_info,
)
from brain.templates.models import Limits
from contract import AutonomyLevel, TemplateInfo

VALID = {
    "name": "mini",
    "display_name": "Mini",
    "description": "A small team",
    "lead": {"persona": {"name": "Lee", "role": "Lead"}, "instructions": "Be brief."},
    "specialists": {
        "writer": {"persona": {"name": "Wes", "role": "Writer"}, "tools": ["read_memory"]},
    },
    "task_types": {
        "social_post": {
            "description": "A post",
            "steps": ["writer"],
            "output": "social_post",
            "action": "post_social",
            "start_level": "act_after_approval",
            "max_level": "autonomous",
        },
    },
}

def write(tmp_path, data, name="team.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data))
    return path

def changed(**task_type_changes):
    data = copy.deepcopy(VALID)
    data["task_types"]["social_post"].update(task_type_changes)
    return data

@pytest.fixture
def marketing():
    return load_template(TEMPLATES_DIR / "marketing.yaml")

def test_marketing_template_loads(marketing):
    assert marketing.name == "marketing"
    assert marketing.display_name == "Marketing"
    assert list(marketing.task_types) == ["social_post", "newsletter", "competitor_check"]
    assert list(marketing.specialists) == ["writer", "researcher"]
    assert list(marketing.onboarding) == ["channels", "upcoming", "newsletter_to"]
    assert marketing.task_types["newsletter"].steps == ["researcher", "writer"]
    assert marketing.task_types["social_post"].action == "post_social"
    assert marketing.task_types["competitor_check"].action is None
    assert marketing.specialists["researcher"].tools == ["web_search", "fetch_page", "read_memory"]
    assert marketing.limits == Limits(
        max_tasks_per_request=3, max_steps_per_specialist=6, token_budget=60000
    )

def test_max_level_defaults_to_start_level(marketing):
    assert marketing.max_level("competitor_check") == AutonomyLevel.ACT_AND_REPORT
    assert marketing.max_level("social_post") == AutonomyLevel.AUTONOMOUS
    assert marketing.max_level("newsletter") == AutonomyLevel.ACT_AND_REPORT

def test_personas_lead_first(marketing):
    assert [p.name for p in marketing.personas()] == ["Maya", "Leo", "Sam"]

def test_template_info(marketing):
    assert template_info(marketing) == TemplateInfo(
        name="marketing",
        display_name="Marketing",
        description=marketing.description,
        personas=marketing.personas(),
        task_types=["social_post", "newsletter", "competitor_check"],
    )

def test_load_templates_keys_by_name(tmp_path):
    write(tmp_path, {**VALID, "name": "alpha"}, "a.yaml")
    write(tmp_path, {**VALID, "name": "beta"}, "b.yaml")
    assert set(load_templates(tmp_path)) == {"alpha", "beta"}
    assert "marketing" in load_templates()

def test_duplicate_template_names_fail(tmp_path):
    write(tmp_path, VALID, "a.yaml")
    write(tmp_path, VALID, "b.yaml")
    with pytest.raises(TemplateError):
        load_templates(tmp_path)

def test_minimal_template_loads(tmp_path):
    assert load_template(write(tmp_path, VALID)).name == "mini"

def test_invalid_yaml_fails(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("name: [unclosed")
    with pytest.raises(TemplateError):
        load_template(path)

def test_unknown_specialist_in_steps(tmp_path):
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, changed(steps=["editor"])))

def test_unknown_tool(tmp_path):
    data = copy.deepcopy(VALID)
    data["specialists"]["writer"]["tools"] = ["post_social"]
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, data))

def test_unknown_output(tmp_path):
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, changed(output="tweet")))

def test_unknown_action(tmp_path):
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, changed(action="post_tiktok")))

def test_action_output_mismatch(tmp_path):
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, changed(output="email", action="post_social")))

def test_start_level_above_max_level(tmp_path):
    data = changed(start_level="autonomous", max_level="act_after_approval")
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, data))

def test_more_than_three_onboarding_questions(tmp_path):
    data = {**VALID, "onboarding": {f"q{n}": f"Question {n}?" for n in range(4)}}
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, data))

def test_empty_steps(tmp_path):
    with pytest.raises(TemplateError):
        load_template(write(tmp_path, changed(steps=[])))
