# Stage 10: Voice notes, photos and files in

**Status:** done
**Depends on:** stage 9; contract v0.6 (agreed Oct 9)
**Spec:** [CONTRACT.md](../CONTRACT.md) §5 (attachments), §7 (`IncomingMessage.attachments`), §9 (`Tools.read_file`), §11 (`FileRef`); [ROADMAP.md](../ROADMAP.md) §1

## Goal

The founder can talk instead of type, and show instead of describe.

- **Voice notes:** a voice note runs exactly like the same words typed.
- **Photos:** a photo is understood and can go into a post as one of its `images`.
- **Documents:** a PDF or screenshot is read as context, and in business onboarding it fills the profile the way a website does.
- **Videos:** the team says it can't watch videos yet.

## Design

**Attachments become text before anything decides.** Jev only reads text, so `handle_message` first turns every attachment into a short text block, appended to `msg.text`. TRIAGE, plan, clarify and history then work unchanged.

| Kind                         | How                                                                                                       | Becomes                                                                            |
| ---------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `audio`                      | `MODEL_MEDIA` with an `input_audio` part: "Transcribe this voice note word for word"                      | `[Voice note] <transcript>`. With no caption, the transcript _is_ the request      |
| `image`                      | `MODEL_MEDIA` with an `image_url` data-URL part: "Describe this in one sentence; read out any text in it" | `[Photo f1: <description>]`. The `FileRef` is kept in state as available media     |
| `document`, PDF              | `MODEL_MEDIA` with a `file` part: "Extract the text; keep prices, dates and names exact"                  | `[Document price-list.pdf]\n<text, max ~8,000 chars>`                              |
| `document`, other (`text/*`) | Decode as UTF-8, no model                                                                                 | Same as PDF                                                                        |
| `video`, unknown             | No model                                                                                                  | Nothing; the lead adds one `Say`: "I can't watch videos yet; tell me what's in it" |

- **One helper, `brain/media.py`:** `describe(deps, business_id, file) -> MediaText | None`, which calls `Tools.read_file` and then the model. A `None` from `read_file` or a model error gives `[Photo f1: couldn't open it]` and the run goes on. A voice note that can't be heard gets a `Say`: "I couldn't hear your voice note. Could you send it again, or type it?"; with no caption and nothing else readable, the run doesn't start.
- **`Progress` before each file:** "Maya is listening to your voice note…", "…looking at your photo…", "…reading your document…".
- **Usage** goes through `MeteredLLM` automatically. Add `"model_media": "Listening and reading"` to `ROLES` in `usage.py`.
- **Founder photos in posts:**
  - Team state keeps `media: dict[file_id, FileRef]` for the current request (reset per request, like `tasks`).
  - The writer's context lists them ("Photos the founder sent: f1, a sunlit studio…"), and `SocialPostOutput` and `EmailOutput` gain `images: list[str] = []` (file ids).
  - In code, `to_planned_action` maps the ids to the `FileRef`s in state and drops unknown ids. The model never produces a `FileRef`.
  - Hard validator: at most 4 images.
  - The check state says "with 1 photo: a sunlit studio…".
- **Revisions:** keep the request's `media` in the checkpoint so a reject-and-revise still has the photos.
- **Company graph:**
  - Images and documents in an onboarding answer go to extraction next to fetched pages (same `pages` slot, with the file name as the title).
  - Audio becomes the answer text.

### Formats on OpenRouter (checked Oct 9)

```python
# audio: base64 only, no URLs; formats wav, mp3, aiff, aac, ogg, flac, m4a, pcm16, pcm24
{"type": "input_audio", "input_audio": {"data": b64, "format": "ogg"}}
# image
{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
# PDF; add plugins=[{"id": "file-parser", "pdf": {"engine": "native"}}] to bill it as input tokens
{"type": "file", "file": {"filename": name, "file_data": f"data:application/pdf;base64,{b64}"}}
```

- **The `LLM` protocol already allows this:** `Message` is a dict, so `content` can be a list of parts. `OpenRouterLLM` passes it through, and the `plugins` go in `extra_body`.
- **`MODEL_MEDIA`:** default `google/gemini-3.5-flash-lite`, which takes text, image, file and audio at about $0.30 per million input tokens. `google/gemini-3.6-flash` is the step up.
- **Reasoning:** `reasoning=False`.
- **Test first:** Telegram voice notes are **OGG/Opus**. Before building on `format: "ogg"` with the chosen model, run one live transcription of a real voice note (`@pytest.mark.live`). If it fails, try `google/gemini-3.6-flash`, then ask Mark to convert voice notes in the app.

## Files

| File                                            | Contains                                                                                                        |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `src/brain/media.py`                            | `MediaText`, `describe()`, `attachments_text()`                                                                 |
| `src/brain/deps.py`                             | `model_media` setting                                                                                           |
| `src/brain/usage.py`                            | `ROLES["model_media"]`                                                                                          |
| `src/brain/brain.py`                            | `handle_message` turns attachments into text first; video `Say`                                                 |
| `src/brain/graphs/team.py`                      | `media` in `TeamState`, `new_request(..., media)`                                                               |
| `src/brain/context.py`, `prompts/specialist.py` | founder photos in the writer's context                                                                          |
| `src/brain/templates/registries.py`             | `images: list[str]` on post and email outputs; ids → `FileRef` in `to_planned_action` (now takes the media map) |
| `src/brain/graphs/company.py`                   | attachments in onboarding answers                                                                               |
| `src/brain/fakes.py`                            | `FakeTools.save_file`, `read_file` (files in a dict); `post_social` and `send_email` accept and record `images` |
| `src/brain/cli.py`                              | `/file <path>` attaches a local file to the next message                                                        |
| `.env.example`                                  | `MODEL_MEDIA`                                                                                                   |

## Tests

`tests/brain/test_media.py`

- `test_voice_note_becomes_the_request`: a message with `text=""` and one audio `FileRef` routes and drafts like the transcript typed (FakeLLM scripts the transcript); the audio part sent has `format: "ogg"` and base64 data.
- `test_caption_and_transcript_are_joined`
- `test_photo_is_described_and_offered_to_the_writer`: the writer's prompt lists `f1` with its description.
- `test_writer_can_attach_the_founders_photo`: the writer returns `images=["f1"]` → `PostSocial.images == [the FileRef]`.
- `test_unknown_image_ids_are_dropped`
- `test_more_than_four_images_fail_the_hard_check`
- `test_pdf_is_read_as_context`: the file part has a `data:application/pdf` URL; the extracted text reaches the plan.
- `test_text_document_needs_no_model`
- `test_video_gets_a_polite_say`
- `test_unreadable_file_does_not_stop_the_run`: `read_file` returns `None` → the run continues, no `Error`.
- `test_media_survives_a_revision`: reject with a reason → the revised draft can still use `f1`.
- `test_onboarding_reads_a_document`: a PDF answer fills profile fields.
- `test_media_usage_is_logged_with_its_purpose`
- Live, skipped by default: `test_live_transcribes_a_telegram_voice_note` with a short `.ogg` fixture in `tests/brain/fixtures/`. It also skips while the fixture is missing; the live checks run once the app and brain are connected.

## Done when

- [x] All tests above pass; earlier stages still pass
- [x] `ruff check` passes
- [ ] Live (once the app and brain are connected): a real voice note of "We launch Friday, get the word out" gives the same drafts as typing it
- [x] Juan has reviewed the tests

## Log

- Oct 9: stage written by Mark from the v0.6 planning session. API formats and model prices checked on OpenRouter the same day.
- Oct 9: reviewed by Juan. Work happens on the `brain-v06` branch. Live tests are written but run only once everything is connected.
- Oct 9: implemented on `brain-v06` under a session goal, tests first (16 red), then green. `tests/brain/test_media.py`: the 13 listed tests plus `test_another_business_file_is_not_read`, `test_edit_keeps_the_founders_photo`, `test_fake_tools_keep_files_per_business`, `test_cli_attaches_a_file_to_the_next_message` and the live test (skips without its fixture). Choices made while building:
  - Output schemas name real `file_id`s (no short labels), so stage 11's generated images work the same way.
  - Only photos the model could describe are offered to the writer; their description becomes the `FileRef.alt_text`, which the check and Bluesky see.
  - Each task keeps its photos (`TaskState.media`), and `task_media` (last 20 tasks, in the checkpoint) brings them back on a reject-and-revise, even after other requests.
  - An edit keeps the draft's images (`_edited_action` copies the action).
  - At `draft_only`, a post's photos go in `NeedsApproval.media` / `Approval.media`.
  - `execute_action` passes `images` only when there are some, so Mark's `RealTools` keeps working until it takes them.
  - The specialist stores its result without default values, so an empty `images` doesn't reach earlier-stage tests or later steps' prompts.
  - No `file-parser` plugin: OpenRouter reads PDFs natively for Gemini by default, so the `LLM` protocol is unchanged.
  - Not done: a photo sent as the answer to a pending `Ask` is described into the text but not offered to the writer.
- Oct 9: Juan reviewed and approved the tests; stage done and committed on `brain-v06`.
