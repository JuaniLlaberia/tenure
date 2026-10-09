# Stage 15: Images are made once

**Status:** built by Mark on `upgrades-integration` (Oct 9), **needs Juan's review**
**Depends on:** stages 11, 12
**Spec:** [CONTRACT.md](../CONTRACT.md) v0.7 §7 (`new_image`, "Images are made once")

## Why

The founder asked for this after seeing the costs. Before this stage, a failed review ran the
illustrator again, and so did text feedback (writer, then illustrator), and every rerun
generated a new image. That could be 3 images per draft (about $0.08 each) before the founder
saw anything, and a text-only rejection threw a good image away.

## The rule

- While drafting, `generate_image` **plans** an image. It records the prompt, alt text and
  aspect ratio under a placeholder `FileRef` (`file_id` starting `plan-`, `size_bytes` 0;
  `tools.is_plan`) and doesn't call the image model.
- The lead's review (`run_check`) judges the text and the image plan together: Jev and the
  critic see the alt text and prompt (`photos_line`). The critic gets no image: there isn't one
  yet. Revisions rewrite text or plan, which are cheap model calls.
- **`render`** (team graph, the first thing `gate_node` does) makes every planned image in the
  final output exactly once. It emits "Otto is drawing: …", calls `deps.images.generate`, stores
  the result with `Tools.save_file`, and swaps the placeholder id for the real one in `outputs`,
  `media` and `prompts`. The task is then `rendered`. A planned image that can't be made is
  dropped; a `visual` then fails at the gate as before (`min_images`), and a post goes without
  it.
- Once a task is `rendered`, nothing automatic touches its image again:
  - `specialist` skips another team's step ("design:illustrator") unless `redraw` is set.
  - `rerun_from` always goes back to the writer, without asking `about_image`.
- **Only the founder redraws.** Two ways:
  - a rejection whose reason is about the image (the existing `about_image` decision);
  - the new `new_image` decision (contract v0.7). `TenureBrain.resolve_approval` marks it as
    image feedback; `flows/approvals._new_image` checks that the draft has an image the team
    made and that revisions are left (otherwise a recoverable `Error`, and the draft stays
    pending), then rejects with the reason (default "Make a different image.", which isn't
    learned from) and revises.

  Either way `revision_task` sets `redraw`, the illustrator plans again, the review checks the
  new plan, and `render` makes one new image. A text-only rejection keeps the image.
- A Design team `visual` (the illustrator is its own step) follows the same plan → review →
  render path. Its revisions always replan, because the image is the whole draft.

## Files changed

| File | Change |
| --- | --- |
| `src/brain/tools.py` | `generate_image` plans; `PLAN_PREFIX`, `is_plan`; `ToolContext.aspects` |
| `src/brain/graphs/specialist.py` | `aspects` passed through the specialist state |
| `src/brain/graphs/team.py` | `TaskState.aspects`, `rendered`, `redraw`; `render` and `drawer`; skip a made image's step; `rerun_from` and `revision_task` respect `rendered` / `redraw` |
| `src/brain/flows/approvals.py` | `new_image` → `_new_image`; `_reject(teach=...)` |
| `src/brain/brain.py` | `new_image` counts as image feedback |
| `src/brain/check.py` | the critic skips planned images (there's nothing to read yet) |
| `src/contract/app_side/inputs.py` | `"new_image"` in `ApprovalDecision.decision` |

## Also fixed on this branch

- `src/brain/media.py`: the media call no longer sends `reasoning=False`. Gemini 3.5 Flash Lite
  can't turn reasoning off, so OpenRouter answered 400 and every voice note, photo and PDF came
  through as "couldn't open it". Found live on Oct 9; `test_voice_note_becomes_the_request`
  now checks it. `test_live_transcribes_a_telegram_voice_note` would have caught it, but it
  skips without `tests/brain/fixtures/voice_note.ogg`.

## Tests

Frozen tests changed (Juan, please check these four):

- `test_design.py::test_generate_image_saves_the_file_with_alt_text` → `test_generate_image_only_plans_the_image`
- `test_design.py::test_image_failure_is_a_tool_error_not_a_crash`: the failure now happens at render, so there's no tool error in the specialist's messages; the draft still fails, recoverably
- `test_design.py::test_critic_sees_the_image_on_revise` → `test_review_judges_the_image_prompt_before_any_image_is_made`
- `test_campaign_visuals.py::test_text_feedback_reruns_writer_then_illustrator` → `test_text_feedback_reruns_the_writer_and_keeps_the_image`

New tests:

- `test_design.py`:
  - `test_the_planned_image_is_made_once_after_review`
  - `test_new_image_makes_exactly_one_more`
  - `test_new_image_with_a_reason_teaches_the_design_team`
  - `test_new_image_needs_an_image_and_revisions_left`
- `test_campaign_visuals.py`:
  - `test_failed_reviews_never_make_extra_images`
  - `test_a_review_after_a_text_revision_never_redraws`
- `tests/contract`: `test_new_image_is_a_decision_with_an_optional_reason`
