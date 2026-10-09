import pytest

from brain.cli import Cli, parse
from contract import (
    ActionDone,
    AutonomyLevel,
    NeedsApproval,
    Persona,
    PostSocial,
    PromotionOffer,
    Say,
    TeamHired,
)

MAYA = Persona(name="Maya", role="Marketing lead")

class RecordingBrain:
    def __init__(self, events=None):
        self.calls = []
        self.events = events or []

    def list_templates(self):
        return []

    async def _reply(self, name, *args):
        self.calls.append((name, *args))
        for event in self.events:
            yield event

    def start_onboarding(self, business_id):
        return self._reply("start_onboarding", business_id)

    def handle_message(self, msg):
        return self._reply("handle_message", msg)

    def hire_team(self, business_id, template):
        return self._reply("hire_team", business_id, template)

    def resolve_approval(self, decision):
        return self._reply("resolve_approval", decision)

    def respond_promotion(self, response):
        return self._reply("respond_promotion", response)

    def undo_action(self, business_id, action_id):
        return self._reply("undo_action", business_id, action_id)

@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/start", ("start", "")),
        ("/hire marketing", ("hire", "marketing")),
        ("/team Marketing", ("team", "Marketing")),
        ("/general", ("general", "")),
        ("/approve", ("approve", "")),
        ("/edit Friday. Be there.", ("edit", "Friday. Be there.")),
        ("/reject too salesy", ("reject", "too salesy")),
        ("/reject", ("reject", "")),
        ("/accept", ("accept", "")),
        ("/decline", ("decline", "")),
        ("/undo", ("undo", "")),
        ("/seed social_post 4", ("seed", "social_post 4")),
        ("/lessons", ("lessons", "")),
        ("/quit", ("quit", "")),
        ("Post about our Friday launch", ("say", "Post about our Friday launch")),
        ("  /HIRE   marketing ", ("hire", "marketing")),
    ],
)
def test_cli_commands_parse(line, expected):
    assert parse(line) == expected

@pytest.fixture
def cli(store):
    output = []
    recording = RecordingBrain()
    return Cli(recording, store, business_id="b1", out=output.append), recording, output

async def test_plain_text_goes_to_the_current_topic(cli):
    shell, brain, _ = cli

    await shell.handle("Hello there")
    shell.team_id = "t1"
    await shell.handle("Post about Friday")

    general, team = brain.calls
    assert general[0] == "handle_message" and general[1].team_id is None
    assert team[1].team_id == "t1" and team[1].text == "Post about Friday"
    assert general[1].message_id != team[1].message_id

async def test_start_and_hire_call_the_brain(cli):
    shell, brain, _ = cli

    await shell.handle("/start")
    await shell.handle("/hire marketing")

    assert brain.calls == [("start_onboarding", "b1"), ("hire_team", "b1", "marketing")]

async def test_hired_team_becomes_the_current_topic(cli):
    shell, brain, _ = cli
    brain.events = [
        TeamHired(team_id="t9", template="marketing", display_name="Marketing", personas=[MAYA])
    ]

    await shell.handle("/hire marketing")

    assert shell.team_id == "t9"

async def test_approval_commands_use_the_latest_approval(cli):
    shell, brain, _ = cli
    shell.render(
        NeedsApproval(
            approval_id="a1",
            team_id="t1",
            task_id="k1",
            task_type="social_post",
            persona=MAYA,
            preview="We launch Friday!",
            planned_action=PostSocial(text="We launch Friday!"),
            check_confidence=0.9,
        )
    )

    await shell.handle("/approve")
    await shell.handle("/edit Friday. Be there.")
    await shell.handle("/reject too salesy")

    decisions = [call[1] for call in brain.calls]
    assert [(d.approval_id, d.decision) for d in decisions] == [
        ("a1", "approve"),
        ("a1", "edit"),
        ("a1", "reject"),
    ]
    assert decisions[1].edited_text == "Friday. Be there."
    assert decisions[2].reason == "too salesy"

async def test_promotion_and_undo_use_the_latest_offer_and_action(cli):
    shell, brain, _ = cli
    shell.render(
        PromotionOffer(
            team_id="t1",
            task_type="social_post",
            persona=MAYA,
            current_level=AutonomyLevel.ACT_AFTER_APPROVAL,
            proposed_level=AutonomyLevel.ACT_AND_REPORT,
            evidence="You approved my last 5 drafts",
        )
    )
    shell.render(
        ActionDone(
            action_id="x1",
            team_id="t1",
            task_id="k1",
            summary="Posted to Bluesky",
            url=None,
            autonomous=False,
            undo_until="2026-10-08T12:10:00Z",
        )
    )

    await shell.handle("/accept")
    await shell.handle("/undo")

    response = brain.calls[0][1]
    assert (response.team_id, response.task_type, response.accepted) == ("t1", "social_post", True)
    assert brain.calls[1] == ("undo_action", "b1", "x1")

async def test_commands_without_a_target_say_so(cli):
    shell, brain, output = cli

    for line in ["/approve", "/accept", "/undo"]:
        await shell.handle(line)

    assert brain.calls == []
    assert len(output) == 3

async def test_render_shows_persona_and_text(cli):
    shell, _, output = cli

    shell.render(Say(team_id="t1", persona=MAYA, text="On it."))

    assert "Maya" in output[-1] and "On it." in output[-1]
