# Stage 11: Design team and image generation

**Status:** not started
**Depends on:** stage 10 (for `FakeTools` files and the media map)
**Spec:** [CONTRACT.md](../CONTRACT.md) §6 (`NeedsApproval.media`, `Say.media`), §9 (`Tools.save_file`), §11 (`FileRef`, `Approval.media`); [ROADMAP.md](../ROADMAP.md) §2

## Goal

A hireable **Design** team that makes images in the business's style, learns from feedback like every team, and earns autonomy on its own task type. Images only; no video.

## Design

**Image generation uses OpenRouter's Images API**, not chat completions:

```http
POST https://openrouter.ai/api/v1/images
{"model": "...", "prompt": "...", "aspect_ratio": "1:1", "resolution": "1K", "n": 1,
 "input_references": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]}
→ {"data": [{"b64_json": "...", "media_type": "image/png"}],
   "usage": {"prompt_tokens": 0, "completion_tokens": 4175, "total_tokens": 4175, "cost": 0.04}}
```

`GET /api/v1/images/models` lists the models and the parameters each supports; `/api/v1/images/models/{id}/endpoints` gives prices.

**`MODEL_IMAGE` candidates** (prices checked Oct 9). Pick one with a short live test (latency, quality on a launch visual, how well it follows the brand style), as we did for the text models:

| Model | Price | Notes |
| --- | --- | --- |
| `google/gemini-3.1-flash-image` (Nano Banana 2) | $60 per million output tokens (~$0.08 per 1K image) | Strong at following instructions and rendering text; takes reference images |
| `google/gemini-3.1-flash-lite-image` | Half the above (~$0.04) | Cheaper Gemini |
| `bytedance-seed/seedream-5-0-flash` | $0.018 per image | Cheap; takes reference images |
| `recraft/recraft-v4.1-flash` | $0.007 per image | Cheapest; illustration and brand styles; text-only input |
| `openai/gpt-image-1-mini` | Token-priced | `quality: low` is cheap |

**Size:** ask for `1:1` (posts) or `16:9` (newsletter header) at `1K`; that keeps files well under Bluesky's 2 MB. The app still shrinks anything bigger with Pillow.

**Pieces**

| Piece | What |
| --- | --- |
| `helpers/images.py` | `ImageClient` protocol plus `OpenRouterImages.generate(model, prompt, aspect_ratio, references) -> GeneratedImage(data, mime_type, usage)`. Plain `httpx`, `LLM_TIMEOUT` with one retry; it raises `ImageError` on failure. `FakeImages` in `fakes.py` returns a tiny PNG |
| Usage | `MeteredImages` in `usage.py` records `usage.cost` like `MeteredLLM`; `ROLES["model_image"] = "Images"` |
| Tool `generate_image` | Arguments `prompt`, `aspect_ratio`, `alt_text`. It generates, then calls `Tools.save_file(business_id, data, mime_type, alt_text=...)` and returns `"Saved image f7: <alt_text>"`. `save_file` returning `None` or `ImageError` gives a tool error string, so the model can retry once. At most 2 calls per step (the specialist step limit already applies) |
| Output `image` | `ImageOutput(images: list[str], caption: str)`. Validators: 1–4 ids that are images made in this task. `preview` is the caption. No actions, so the files go in `media` |
| Gate | When the action is `None` (or the level is `draft_only`), `NeedsApproval.media` and `Approval.media` carry the output's images; at an acting level with no action, the lead's `Say` carries them in `media` |
| Check | Hard validators; then Jev on the prompt, the alt text and the caption against the check rules and lessons (Jev can't see images). On a revise, the Sonnet critic gets the image itself as an `image_url` part. Critic issues go back to the illustrator |
| Learning | Unchanged: edits are rare for images; reject with a reason ("warmer colours, no people") → lessons for the design team. Approved examples use the caption and alt text |

**`design.yaml`**

```yaml
name: design
display_name: Design
description: "Images for your posts, newsletters and announcements, in your brand's style"
lead:
  persona: { name: Iris, role: Design lead, avatar: design/iris.png }
  instructions: >-
    One clear idea per image. Brief the illustrator with the subject, the mood, the brand's
    colours and what to leave out.
specialists:
  illustrator:
    persona: { name: Otto, role: Illustrator, avatar: design/otto.png }
    tools: [generate_image, read_memory]
    instructions: >-
      Write image prompts like an art director: subject, composition, style, palette, light.
      No text in the image unless asked; never logos or real people you weren't given.
      Give every image one-sentence alt text that says what it shows.
task_types:
  visual:
    description: "An image for a post, a newsletter or an announcement"
    steps: [illustrator]
    output: image
    check: ["fits the brand's style and colours", "one clear subject", "no text or logos unless asked"]
    action: null
    start_level: act_after_approval
    max_level: act_and_report
onboarding:
  palette: "What are your brand colours? A hex code, a description or a link to your site is fine."
  style:
    question: "What visual style fits you best?"
    quick_replies: ["Photo-real", "Illustration", "Flat and minimal"]
  avoid: "Anything we should never show?"
```

`TOOL_NAMES` gains `generate_image`; `OUTPUTS` gains `image`. Approving a `visual` draft at `act_after_approval` just marks it done, as for any task type without an action.

## Files

| File | Contains |
| --- | --- |
| `src/brain/helpers/images.py` | `ImageClient`, `OpenRouterImages`, `GeneratedImage`, `ImageError` |
| `src/brain/deps.py` | `model_image` setting; `Deps.images` |
| `src/brain/usage.py` | `MeteredImages`, `ROLES["model_image"]` |
| `src/brain/tools.py` | `generate_image` |
| `src/brain/templates/registries.py` | `ImageOutput`, `image` output type |
| `src/brain/templates/design.yaml` | the template |
| `src/brain/graphs/team.py` | gate puts images in `media` |
| `src/brain/check.py` | critic sees the image |
| `src/brain/fakes.py` | `FakeImages` |
| `.env.example` | `MODEL_IMAGE` |

## Tests

`tests/brain/test_design.py`

- `test_design_template_loads_and_is_hireable`: `list_templates()` includes Design with its personas and avatars.
- `test_generate_image_saves_the_file_with_alt_text`: `FakeTools.save_file` got the bytes, the mime type and the alt text.
- `test_visual_draft_carries_media_not_an_action`: `NeedsApproval.planned_action is None`, `media` has the `FileRef`, and the saved `Approval.media` matches.
- `test_visual_at_act_and_report_is_a_say_with_media`
- `test_image_failure_is_a_tool_error_not_a_crash`: `FakeImages` raises → the illustrator sees the error; after the limit the task fails with a recoverable `Error`.
- `test_image_output_needs_one_to_four_known_images`
- `test_critic_sees_the_image_on_revise`: the critic call has an `image_url` part.
- `test_image_usage_is_logged_with_its_cost`
- `test_reject_with_reason_teaches_the_design_team`
- Live, skipped: `test_live_generates_a_square_image_under_2mb`

## Done when

- [ ] All tests above pass; earlier stages still pass
- [ ] `ruff check` passes
- [ ] Live: hire Design, answer onboarding, "Make an image for our Friday launch" → an approval card with the image in Telegram
- [ ] Juan has reviewed the tests

## Log

- Oct 9: stage written by Mark from the v0.6 planning session. Image API shape and prices checked on OpenRouter the same day.
