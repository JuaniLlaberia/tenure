# Stage 12: Marketing and design on one campaign

**Status:** done
**Depends on:** stage 11
**Spec:** [CONTRACT.md](../CONTRACT.md) §6 (a persona from another team); [ROADMAP.md](../ROADMAP.md) §3

## Goal

Once a business has hired Design, every marketing post and newsletter comes with an image made in the same run. Design's illustrator makes it with Design's lessons, and the founder approves text and image as one draft in the marketing topic. Without Design, marketing works as today, and Maya suggests hiring Design once.

## Design

**Steps stay fixed and declared in YAML.** Task types name extra steps that another hired team runs:

```yaml
# marketing.yaml
social_post:
  steps: [writer]
  with_teams:
    design: [illustrator]
newsletter:
  steps: [researcher, writer]
  with_teams:
    design: [illustrator]
```

- **`TaskTypeSpec.with_teams: dict[str, list[str]] = {}`** maps a template name to specialist ids in *that* template. The loader validates that both exist, across templates, after all templates load.
- **Plan:** for each task, if `store.list_teams(business_id)` has a team with that template (onboarded), the steps become `steps + [f"{template}:{id}" ...]` (`"design:illustrator"`). `Task.steps` stores these strings, so the dashboard and the checkpoint see the real plan.
- **Dispatch / specialist node:** a `"design:illustrator"` step resolves persona, tools and instructions from the design template. Its context is built with the **design team's** `team_id` (its lessons, profile and approved `visual` examples) plus the earlier steps' outputs of this task (the writer's draft). Events keep `team_id` = marketing (shown in the marketing topic) with Otto's persona, as CONTRACT §6 allows.
- **Output:**
  - The illustrator step returns `ImageOutput` (stage 11), stored as that step's output like any other step's.
  - The task's output type doesn't change (`social_post`, `email`): code copies the illustrator's `images` into the writer's output's `images` field (from stage 10).
  - So the final output is the writer's output with those images, and they reach `PostSocial.images` / `SendEmail.images` through `to_planned_action`.
- **Check:** unchanged, with "with 1 image: <alt text>" in the state.
- **Revise:** on a failed check or a reject with a reason, one extra `decide()` question, `about_image` ("Is this feedback only about the image?"), picks the step to rerun: the illustrator if yes, otherwise the writer (and then the illustrator again, so the image matches the new text).
- **Learning:**
  - Feedback about the image (same `about_image` decision) calls `learn()` with the design team's `team_id` and template, so the lesson belongs to Design. Its `LessonLearned` has the design `team_id` (shown in the Design topic).
  - Maya adds a `Say` in marketing: "Iris noted that for next time: warmer colours."
  - Everything else learns as today.
- **Trust:** the approval is on the marketing task type; the image rides along. Design's own trust is untouched.
- **Usage:** rows keep the marketing `team_id` and the task's id, so a task's cost stays whole.
- **Without Design:**
  - The first time a `social_post` or `newsletter` is planned in a business without a Design team, the lead's report adds one line: "Want images with these? Hire the Design team."
  - "Once" is `suggested_teams: list[str]` in the team state, kept in the checkpoint (generic, so no per-team code). It's not a lesson: `list_lessons` returns every active lesson, so a hidden lesson would show up in the dashboard's Knowledge tab.
- **Budget:** an image step costs one or two image calls on top of the text. Keep `token_budget` (images are priced per image, not per token) and add `limits.max_images_per_request: 4`.

## Files

| File | Contains |
| --- | --- |
| `src/brain/templates/models.py` | `with_teams`; cross-template validation |
| `src/brain/templates/loader.py` | validates `with_teams` once all templates are loaded |
| `src/brain/templates/marketing.yaml` | `with_teams` on `social_post` and `newsletter` |
| `src/brain/graphs/team.py` | plan adds cross-team steps; specialist resolves `"template:id"`; merge images; `about_image` on revise; `suggested_design` in state |
| `src/brain/context.py` | context for another team's step |
| `src/brain/flows/approvals.py` | reject with an image reason → learn for Design, rerun the illustrator |

## Tests

`tests/brain/test_campaign_visuals.py`

- `test_without_design_steps_are_unchanged`
- `test_with_design_the_post_gets_an_illustrator_step`: `Task.steps == ["writer", "design:illustrator"]`.
- `test_illustrator_uses_the_design_teams_lessons`: a design lesson ("warm colours") is in the illustrator's prompt; a marketing-only lesson is not.
- `test_illustrator_progress_shows_in_the_marketing_topic`: the `Progress` has the marketing `team_id` and Otto's persona.
- `test_post_draft_carries_the_image`: `NeedsApproval.planned_action.images` has the generated `FileRef`; `media` is empty.
- `test_newsletter_draft_carries_the_image`
- `test_image_feedback_reruns_only_the_illustrator`
- `test_text_feedback_reruns_writer_then_illustrator`
- `test_image_feedback_becomes_a_design_lesson`: the saved lesson's `team_id` is the design team's.
- `test_design_is_suggested_once`: the second request in the same team thread has no suggestion, and no lesson is saved for it.
- `test_unknown_with_teams_names_fail_at_load`

## Done when

- [x] All tests above pass; earlier stages still pass
- [x] `ruff check` passes
- [ ] Live demo run (once the app and brain are connected): with Design hired, "We launch Friday, get the word out" → post and newsletter drafts with images; approve → Bluesky post with the image, email with the header image
- [x] Juan has reviewed the tests

## Log

- Oct 9: stage written by Mark from the v0.6 planning session.
- Oct 9: reviewed by Juan. The output merge is spelled out (illustrator → `ImageOutput`, images copied into the writer's output). "Suggest Design once" moved from a hidden lesson to `suggested_design` in the team state, so it never shows in the Knowledge tab.
- Oct 9: implemented on `brain-v06` under a session goal, tests first (13 red), then green. `tests/brain/test_campaign_visuals.py`: the 11 listed tests plus `test_check_feedback_about_the_image_reruns_only_the_illustrator`, `test_images_per_request_are_capped`, `test_image_cost_stays_with_the_marketing_task` and `test_approved_posts_remember_their_image_in_examples`. No contract or `.env.example` changes. Choices made while building:
  - A cross-team step runs as `StepRun`: the other template's specialist, its own task type's output (`image`), and the other team's memory (`build_context` and `read_memory` use the Design team's id and `visual`). Its events keep the marketing `team_id`. The writer now writes the final output when it's the last *own* step.
  - If the other team was fired or isn't onboarded by the time the step runs, the step is skipped (logged) and the post goes out without an image.
  - The suggestion line comes from the other template's description: "Want images for your posts, newsletters and announcements, in your brand's style? Hire the Design team with /hire design."
  - Revisions: `rerun_from` asks `about_image` after a failed check; for a reject, `TenureBrain._image_team` asks once per approval and the answer drives both the learning (Design's lesson + Maya's "Iris noted…" `Say`) and the revision's start step (`image_feedback` in the run's input). The routing lives in `brain.py`, not `flows/approvals.py`.
  - Replaced stage 10's `task_media` with `past_tasks`: the report node keeps the last 20 tasks' whole `TaskState` (outputs, media, prompts) in the checkpoint, and a revision resumes from it. An image-only revision needs the writer's earlier draft, and text revisions of a newsletter now see the researcher's notes again.
  - `limits.max_images_per_request` (default 4) lowers the step's `image_limit` once the request has made that many images.
  - Approved examples list the images of a post's action too, not only `Approval.media`.
- Oct 9: Juan reviewed and approved the tests; stage done and committed on `brain-v06`.
