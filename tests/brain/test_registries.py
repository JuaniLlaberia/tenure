import pytest
from pydantic import ValidationError

from brain.templates.registries import ACTIONS, OUTPUTS, TOOL_NAMES
from contract import PostSocial, SendEmail

def test_registry_names():
    assert set(OUTPUTS) == {"social_post", "email", "report", "notes"}
    assert ACTIONS == {"post_social": "social_post", "send_email": "email"}
    assert TOOL_NAMES == {"read_memory", "web_search", "fetch_page"}

def test_social_post_maps_to_post_social():
    output_type = OUTPUTS["social_post"]
    output = output_type.schema(text="We launch Friday")
    planned = output_type.to_planned_action(output, "post_social")
    assert planned == PostSocial(text="We launch Friday")

def test_email_maps_to_send_email():
    output_type = OUTPUTS["email"]
    output = output_type.schema(to="list@b.co", subject="Launch", body="We launch Friday")
    assert output_type.to_planned_action(output, "send_email") == SendEmail(
        to="list@b.co", subject="Launch", body="We launch Friday"
    )

def test_report_has_no_planned_action():
    output_type = OUTPUTS["report"]
    output = output_type.schema(summary="Competitors are quiet", sources=["https://a.co"])
    assert output_type.to_planned_action(output, None) is None

def test_no_action_means_no_planned_action():
    output_type = OUTPUTS["social_post"]
    assert output_type.to_planned_action(output_type.schema(text="Hi"), None) is None

def test_previews_show_what_will_be_sent():
    post = OUTPUTS["social_post"]
    assert post.preview(post.schema(text="We launch Friday")) == "We launch Friday"

    email = OUTPUTS["email"]
    preview = email.preview(email.schema(to="list@b.co", subject="Launch", body="See you Friday"))
    for part in ["list@b.co", "Launch", "See you Friday"]:
        assert part in preview

    report = OUTPUTS["report"]
    preview = report.preview(report.schema(summary="Quiet week", sources=["https://a.co"]))
    assert "Quiet week" in preview
    assert "https://a.co" in preview

@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("social_post", {}),
        ("email", {"to": "a@b.co"}),
        ("report", {}),
        ("notes", {}),
    ],
)
def test_outputs_validate_their_schema(name, fields):
    with pytest.raises(ValidationError):
        OUTPUTS[name].schema(**fields)
