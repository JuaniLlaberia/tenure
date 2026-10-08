from app.chat import ui
from contract import (
    ActionDone,
    AutonomyLevel,
    Error,
    NeedsApproval,
    Persona,
    PostSocial,
    PromotionOffer,
    Say,
    SendEmail,
)

MAYA = Persona(name="Maya", role="Marketing lead")
UUID = "123e4567-e89b-12d3-a456-426614174000"

def approval(preview: str, action=None, confidence: float = 0.91) -> NeedsApproval:
    return NeedsApproval(
        approval_id=UUID,
        team_id="t1",
        task_id="k1",
        task_type="social_post",
        persona=MAYA,
        preview=preview,
        planned_action=action,
        check_confidence=confidence,
    )

def test_persona_header_is_a_name_line():
    say = Say(team_id=None, persona=MAYA, text="Hi")
    assert ui.say_text(say) == "<b>Maya</b> <i>· Marketing lead</i>\nHi"

def test_brain_text_is_escaped():
    say = Say(team_id=None, persona=Persona(name="<b>", role="x&y"), text="1 < 2")
    assert ui.say_text(say) == "<b>&lt;b&gt;</b> <i>· x&amp;y</i>\n1 &lt; 2"

def test_approval_shows_meta_and_percentage():
    text = ui.approval_text(approval("Hello", PostSocial(text="Hello")))
    assert "<blockquote>Hello</blockquote>" in text
    assert "Bluesky post · 5/300 characters · check 91%" in text

def test_long_previews_are_expandable_and_clipped():
    text = ui.approval_text(approval("x" * 5000, SendEmail(to="a@b.co", subject="S", body="B")))
    assert "<blockquote expandable>" in text
    assert len(text) < 4096

def test_draft_only_says_nothing_gets_sent():
    assert "Draft only, nothing gets sent" in ui.approval_text(approval("Hi"))

def test_callback_data_fits_telegram_limit():
    keyboards = [
        ui.approval_keyboard(UUID),
        ui.reject_keyboard(UUID),
        ui.undo_keyboard(UUID),
        ui.forget_keyboard(UUID),
        ui.promotion_keyboard("a" * 12),
        ui.ask_keyboard("a" * 12, [f"reply {i}" for i in range(12)]),
    ]
    for keyboard in keyboards:
        for row in keyboard:
            for button in row:
                assert len(button.data.encode()) <= 64

def test_action_done_links_and_marks_autonomy():
    done = ActionDone(
        action_id=UUID,
        team_id="t1",
        task_id="k1",
        summary="Posted to Bluesky",
        url="https://bsky.app/profile/x/post/1?a=1&b=2",
        autonomous=True,
        undo_until=None,
    )
    text = ui.action_text(done)
    assert 'href="https://bsky.app/profile/x/post/1?a=1&amp;b=2"' in text
    assert "Done without asking" in text

def test_promotion_uses_plain_words():
    offer = PromotionOffer(
        team_id="t1",
        task_type="social_post",
        persona=MAYA,
        current_level=AutonomyLevel.ACT_AFTER_APPROVAL,
        proposed_level=AutonomyLevel.ACT_AND_REPORT,
        evidence="You approved my last 5 posts without edits.",
    )
    text = ui.promotion_text(offer)
    assert "<b>Now:</b> I ask before acting" in text
    assert "<b>Next:</b> I act, then tell you" in text

def test_unrecoverable_error_points_to_help():
    error = Error(team_id=None, message="Broken", recoverable=False)
    assert ui.error_text(error) == "⚠️ Broken\nUse /help if this keeps happening."

def test_topic_link_strips_supergroup_prefix():
    assert ui.topic_link(-1001234567890, 42) == "https://t.me/c/1234567890/42"
