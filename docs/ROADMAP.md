# Roadmap: next features (Oct 9 → Demo Day)

Agreed by Mark and Juan on Oct 9. The shapes are in [CONTRACT.md](./CONTRACT.md) v0.6 (merged with this file), and this file says what each feature is, in what order we build it, and who does what. Every new contract field has a default, so each feature can ship or be cut on its own.

## Start here

- **Juan (brain):** open [docs/brain/README.md](./brain/README.md) and start at **stage 10**. Stages 10–14 each have the design, files, interfaces and tests ready, with the OpenRouter formats, model candidates and prices already checked. Workflow as before: tests first, review, then green.
- **Mark (app):** follow the **App** part of each feature below, in the timeline's order. The [app reference](#app-reference) at the end has the Telegram, Bluesky, Resend and Supabase details. Publish a mockup before any UI (approval card with images, tabbed dashboard, schedule card, "Meet your team").
- **Telegram parity (Mark, Oct 9):** everything the dashboard can do must also work from Telegram alone; the dashboard is the organized view of the same things, never the only place for an action. So the app adds an edit menu on drafts (text, subject, recipient, ✕ image) and the commands `/drafts`, `/team` (lower trust, promotion threshold), `/schedules`, `/knowledge`, `/activity` and `/spend`.
- **Both:** develop against the other side's fakes. The brain has `FakeTools` and `InMemoryStore` (stages 10 and 14 extend them); the app has `FakeBrain`, which already saves a schedule for "every Monday …" and has `run_schedule`. Merge at the three merge points in the timeline.

**Time box.** Submission is Sat Oct 10, 11:59 PM, with the demo video. Code freezes Sat 5 PM so there is time to record, deploy and submit. Anything not working by then is cut from the demo, not rushed in.

## Order

| # | Feature | Why here | Brain (Juan) | App (Mark) | Size |
| --- | --- | --- | --- | --- | --- |
| 0 | ~~Merge `fixes`, agree v0.6~~ | Done Oct 9 | – | – | – |
| 1 | Voice notes, photos and files in | Small, very demoable, and it builds the file plumbing 2 and 3 need | Understand attachments | Download, store, pass on | S |
| 2 | Design team (images) | The biggest visual upgrade | Template, image generation, check | Show images, post and send them | L |
| 3 | Marketing and design on one campaign | What puts 2 into the main demo | Cross-team step, feedback routing | Nothing beyond 2 | M |
| 4 | Dashboard tabs and avatars | Polish that runs in parallel with 2–3 | Avatars in templates, the art | Tabs, avatars everywhere | M |
| 5 | Schedules | Strong story, but invisible live; we demo it with "Run now" | Understand, save, run | Scheduler, card, tab | M |

**Cut: video generation.** It costs dollars per clip, takes minutes, needs another provider account (Gemini's Veo or Higgsfield) and a different Bluesky upload flow. Founders can still *send* videos (`FileKind.video`); the team says it can't watch them yet.

## Timeline

| When | Juan (brain) | Mark (app) |
| --- | --- | --- |
| Fri night | Stage 10: fakes for files, then attachments (voice → transcript, photo → description, founder photos in posts). Live test: a Telegram voice note through `MODEL_MEDIA`. Quick `MODEL_IMAGE` test | File storage, Telegram downloads, attachments in `IncomingMessage`. `FakeBrain` v0.6 (image draft, attachment replies, avatars). Mockups: approval card with images, tabbed dashboard, schedule card |
| **Merge 1, Fri midnight** | A voice note gives the same drafts as typing; a photo ends up in a post | |
| Sat morning | Stage 11: `design.yaml`, `generate_image` tool, `image` output and its check. Stage 13: avatars in the templates and the art | Images in approval cards, Bluesky, email and the dashboard. Dashboard tabs. Deploy to a fixed host |
| **Merge 2, Sat 1 PM** | Design topic makes an image; it's approved in Telegram and posted | |
| Sat afternoon | Stage 12: marketing × design. Then stage 14: schedules | Avatars in the dashboard and Telegram. Schedules: store, scheduler, card, tab |
| **Merge 3, Sat 4 PM** | Full demo run on the real brain | |
| Sat 5 PM | Freeze. Record the video, submit | |

## 1. Voice notes, photos and files in

**What the founder sees.** In any topic they can send a voice note, a photo, a screenshot or a PDF instead of typing.

- **A voice note works exactly like a typed message.** "We launch Friday, get the word out", spoken, gives the same drafts.
- **A photo can go into a post.** "Post this with a line about our new studio" puts the photo on the draft.
- **A PDF or screenshot is read as context.** A price list or brand guide counts as context; in onboarding it fills the profile the way a website does.
- **A video gets a polite "I can't watch videos yet".**

**Brain stage:** [10](./brain/stage-10-attachments.md).

**Contract.**
- `IncomingMessage.attachments` and `FileRef` (§7, §11).
- `Tools.read_file` (§9).
- The attachment rules in §5.

**Brain**
- A `MODEL_MEDIA` setting for a multimodal model on OpenRouter (a Gemini Flash, for example) and one helper per kind:
  - **Audio:** transcribe it (`input_audio`; OpenRouter lists `ogg`, but Telegram voice notes are OGG/Opus, so test that first).
  - **Image:** a one-line description, and keep the `FileRef` in state.
  - **PDF:** read it as text (OpenRouter `file` part).
- Turn attachments into text before TRIAGE (`[Voice note] …`, `[Photo f1: a sunlit studio]`), so routing and `decide()` stay text-only (Jev reads text).
- The writer may attach the founder's photos. Output schemas name file ids, and code maps them to the real `FileRef`s, so the model never makes one up.
- The company graph feeds documents and screenshots to profile extraction, like fetched pages.
- `Progress` while it works: "Maya is listening to your voice note…".

**App**
- **Telegram handlers** for photo (largest size), voice, audio, document and video. Over 20 MB: say so and don't call the brain.
- **Storage:** download each file and store it in Supabase Storage (a private `files` bucket, path `{business_id}/{file_id}`). A `files` table holds the `FileRef` rows; the memory store is used in tests.
- **Debounce** joins captions and files; a Telegram album is one message.
- **`RealTools.read_file`** returns the bytes, and `save_file` stores them.
- **During an edit:** a photo sent while an edit is open gets "I need text here", and the edit stays open. Images are removed from a draft with the edit menu's "✕ Image n" (Telegram) or "Remove" (dashboard).
- **`FakeBrain`** acknowledges attachments by kind.

**Done when** the spoken demo request gives the same drafts as the typed one, and "post this photo …" puts the photo on the approval card and then on Bluesky.

## 2. Design team

**What the founder sees.** A hireable **Design** team with its own topic. Iris, the design lead, asks about brand colours, visual style (photo, illustration, flat) and anything never to show. "Make an image for our Friday launch" brings an approval card with the image. Feedback like "warmer colours, no people" becomes a lesson, and the next image follows it.

**Images only.** They come from OpenRouter's Images API (`POST /api/v1/images`) with `MODEL_IMAGE`, on the same key, with the billed cost landing in the spend panel automatically. Candidates run from $0.007 to about $0.08 per image (stage 11 has the table); we pick one with a quick test of cost, speed and quality, as we did for text. Higgsfield would need another account and leans toward video, so we skip it.

**Brain stage:** [11](./brain/stage-11-design-team.md).

**Contract.**
- `Tools.save_file` (§9).
- `NeedsApproval.media`, `Approval.media` and `Say.media` (§6, §11).

**Brain**
- **`helpers/images.py`:** generates one image from a prompt (and optional reference images) and logs usage like `MeteredLLM`.
- **`generate_image` tool:** the illustrator calls it, it stores the image through `Tools.save_file` with its `alt_text`, and it returns the file id to the model.
- **`image` output type:**
  - Shape: 1–4 file ids plus a caption.
  - Hard validators: the files exist and are images.
  - Preview: the caption.
  - Action: none, so the images travel in `media`.
- **Check:**
  - Jev judges the prompt, the alt text and the caption against the check rules and brand lessons. Jev can't see images.
  - On a revise, the Sonnet critic sees the image itself.
- **Approve:** marks the task done, and the prompt and alt text become approved examples (style memory).
- **`design.yaml`:**
  - Iris (lead) and an illustrator.
  - Task type `visual`, starting at `act_after_approval`; its `max_level` is `act_and_report`, where the images arrive as a `Say` with `media`.
  - Three onboarding questions.

**App** (mockups first)
- **Approval cards with images:**
  - One image: a photo with the caption and the buttons.
  - Two to four: a media group, then the buttons in their own message.
  - Telegram captions stop at 1,024 characters, so a long preview goes in the next message.
- **Images elsewhere in Telegram:** `Say.media` as a photo or media group.
- **Dashboard:** images in the review pane through an authenticated `/files/{file_id}` route, plus "remove image" when editing (`edited_action.images` is a subset of the draft's images).
- **Bluesky:** upload each image as a blob with its alt text, and shrink any image over 2 MB.
- **Email:** the first image as a header and the rest below the body, inline through Resend attachments with `content_id`, so no public URLs.

**Done when** the Design topic makes an image, it is approved in Telegram, and a lesson from feedback changes the next image.

## 3. Marketing and design on one campaign

**What the founder sees.** Once Design is hired, every marketing post and newsletter comes with a visual made in the same run. Iris shows up in the marketing topic ("Iris is making the image…"), and the founder approves the text and the image together. Without Design, marketing works as it does today. Maya suggests hiring Design once ("Want visuals with these? Hire the Design team"), which doubles as an upsell moment in the demo.

**Brain stage:** [12](./brain/stage-12-campaign-visuals.md).

**How it keeps the rules.** Steps stay fixed and declared in YAML, control still returns to the lead after every step, and there is no per-team code:

```yaml
social_post:
  steps: [writer]
  with_teams:
    design: [illustrator]   # appended when the business has a Design team
```

**Brain**
- **Planning:** at plan time, if `list_teams(business_id)` has a `design` team, the task gets the extra step.
- **The illustrator:**
  - It runs with the design team's persona, instructions and lessons (brand style).
  - It sees the writer's draft as an earlier step's output.
  - Its images land in the output's `images`, so they go into `PostSocial.images` and `SendEmail.images`.
- **Revise:** one Jev question ("is the feedback about the image?") reruns the illustrator or the writer.
- **Learning:**
  - Image feedback becomes a lesson of the design team; text feedback stays with marketing.
  - The `LessonLearned` for an image lesson has the design `team_id`, so it shows in the Design topic, and Maya confirms in marketing ("Iris noted: warmer colours").
- **Trust:** one approval on the marketing task type covers both. Design's own ladder only covers standalone `visual` tasks.
- **Usage:** rows keep the marketing `team_id` and the task, so a task's cost stays whole.

**App.** Nothing beyond 2. Events from Iris in the marketing topic already render, because `team_id` is where an event shows (§6).

**Done when**, with Design hired, the demo line "We launch Friday, get the word out" gives a post and a newsletter that each carry an image. Approving posts to Bluesky with the image and sends the email with the header image.

## 4. Dashboard tabs and avatars

**Tabs** (app only, no contract; mockup first)

| Tab | What it shows |
| --- | --- |
| **Home** | A calm summary: what needs you now (drafts, offers), this week in three numbers, the last five things the teams did |
| **Drafts** | Today's review pane |
| **Teams** | Team cards, the trust ladder per task type, "Hire a team" |
| **Knowledge** | Lessons ("What Maya knows about you"), with Forget |
| **Activity** | The audit log, with Undo |
| **Spend** | Spend per model |
| **Schedules** | Once 5 ships: next run, Run now, Stop |

The tab lives in the URL hash (`#drafts`), so a reload or a Telegram link opens the right one. The counters on Home link into their tab.

**Avatars** (brain stage [13](./brain/stage-13-avatars.md)). No humans: animals and small robots that stand for the role, in one flat style, as round badges that match the dashboard palette.

| Persona | Idea |
| --- | --- |
| Alex, Chief of staff | Owl with a clipboard |
| Maya, Marketing lead | Fox with a megaphone |
| Leo, Writer | Raven with a quill |
| Sam, Researcher | Small robot with a magnifying glass |
| Iris, Design lead | Chameleon |
| Illustrator | Octopus holding brushes |

**Brain**
- **Templates:** an `avatar` per persona in the YAML (and on Alex in `company.py`), so `Persona.avatar` flows through every event and through `TemplateInfo`.
- **The art:** made once, not per business, with the image model from 2 (`uv run python -m brain.avatars`), and committed to `assets/avatars/{template}/{name}.png` at 512 × 512.
- **Fallback:** if image generation slips, Mark makes the six files by hand, since they're only files.

**App**
- **Serving:** `assets/avatars/` is served at `/avatars/`.
- **Dashboard:** avatars on team cards, drafts, lessons and activity.
- **Telegram:**
  - On hire, a "Meet your team" media group with one avatar per persona ("Maya · Marketing lead").
  - One bot can't show a different picture per message, and topic icons only allow Telegram's preset emoji, so each team gets a fitting preset icon.

**Done when** every persona has a picture on the dashboard and the team intro, and the dashboard opens on a Home tab that fits one screen.

## 5. Schedules

**What the founder sees.**
1. The founder writes "Each Monday at 9, research an interesting topic around my business and propose a newsletter".
2. Maya confirms with a card: "Every Monday at 9:00 · Monday newsletter idea", with **Run now** and **Stop**.
3. Each run's drafts wait for approval as usual. Once newsletters have earned `act_and_report`, the Monday newsletter simply goes out and is reported: the employee story in one line.
4. "Stop the Monday newsletter" and "make it Tuesdays" work in chat. The card, `/schedules` and the dashboard also run, stop and turn schedules back on.

**Brain stage:** [14](./brain/stage-14-schedules.md).

**Contract.**
- `Cadence` and `Schedule` (§11).
- The `Store` schedule methods (§10).
- The `ScheduleSaved` event (§6).
- `Brain.run_schedule` (§4).
- `Task.schedule_id` (§11).
- The scheduled-run rules in §5.

**Brain**
- **TRIAGE** gains `wants_schedule` ("is the founder asking for something to happen again and again?") and, when the team has active schedules, `changes_schedule`.
- **Structured extraction** produces the `Cadence`, a title, and the request without its repeat part. Code validates them; a missing time defaults to 9:00, and a weekly request without a day gets an `Ask`.
- **`run_schedule`:** a new request with `schedule_id` on its tasks.
  - It never asks: an unclear point gets the safe reading, and the lead says which one.
  - It never saves another schedule.
  - It keeps the last few titles per schedule in the team's thread, so Monday's topic isn't last Monday's again.

**App**
- **Storage:** a `schedules` table (RLS on) and the memory store.
- **Scheduler:** an asyncio loop in `main.py`, every 60 s.
  - It fills `next_run_at` with `zoneinfo` (stdlib).
  - Due runs go through the per-thread queue; they wait while the thread is blocked and are skipped after 6 h.
  - It sets `last_run_at` and the next run.
- **Telegram:** the schedule card with Run now and Stop.
- **Dashboard:** the Schedules tab.

**Done when** the request shows the card, Run now gives a newsletter draft tagged with the schedule, Stop deactivates it, and a restart keeps it.

## Decisions (Oct 9)

1. **Contract:** v0.4, v0.5 and v0.6 agreed by Juan.
2. **Pillow:** added to the app (Oct 9). It shrinks images over Bluesky's 2 MB limit and makes dashboard thumbnails.
3. **Voice notes:** test early that the audio model takes Telegram's OGG/Opus (stage 10's first live test). If it doesn't, the app converts voice notes before storing them.
4. **Models:**
   - `MODEL_MEDIA` defaults to `google/gemini-3.5-flash-lite`.
   - `MODEL_IMAGE` is picked by the stage 11 test.
5. **Storage:** a private Supabase Storage bucket `files` in the existing project. No new account.
6. **Video generation:** cut.

## Risks

- **Speed.** An image adds roughly 10–30 s to a run that already takes about a minute. Progress lines keep the chat alive; the demo video can be cut.
- **Voice note format.** Telegram voice notes are OGG/Opus, and not every audio model takes them. Test this before building on it.
- **Hosting.** The quick tunnel is still the biggest demo risk, so the deploy is on Saturday morning.

## App reference

Checked on Oct 9.

**Telegram** (python-telegram-bot 22)

| Topic | Detail |
| --- | --- |
| **Incoming files** | `message.photo[-1]` (largest size), `voice` (OGG/Opus), `audio`, `document`, `video`, `video_note`. Albums share `media_group_id` and arrive as separate updates; the debounce joins them. The caption is `message.caption`, not `text`. The handler filter needs `filters.PHOTO \| filters.VOICE \| filters.AUDIO \| filters.Document.ALL \| filters.VIDEO` next to `filters.TEXT` |
| **Downloading** | `await (await bot.get_file(file_id)).download_as_bytearray()`. Bots can download up to **20 MB**; check `file_size` first |
| **Sending images** | `send_photo(chat_id, photo=bytes, caption=..., reply_markup=...)`; captions are limited to **1,024** characters. `send_media_group` takes 2–10 items but **no inline keyboard**, so the buttons go in a follow-up message. To change a card later use `edit_message_caption` / `edit_message_reply_markup` (a photo message has no `text` to edit). The `Chat` port in `chat/port.py` needs `send_photo` and `send_media_group` |
| **Team pictures** | One bot has one profile photo, so personas can't have their own per message. Topic icons accept only `getForumTopicIconStickers` emoji (`icon_custom_emoji_id` on `create_forum_topic`) |

**Bluesky** (atproto 0.0.72)

- **Posting with images:** `client.send_images(text=TextBuilder, images=[bytes, ...], image_alts=[...])` posts up to 4 images (or `upload_blob` plus an `AppBskyEmbedImages` embed).
- **Limits:** each image must be at most **2,000,000 bytes** (doubled from 1 MB in April 2026), in JPEG, PNG or WebP, up to 4000 × 4000. Alt text can be up to 2,000 characters.
- **Shrinking:** use Pillow, re-encoding as JPEG at quality 85 and stepping down until the image fits.

**Resend.** Inline images go in as `attachments: [{"filename", "content": base64, "content_id": "img1"}]`, referenced in the HTML as `<img src="cid:img1">`. That means no public URLs and no expiring links. The first image goes above the body and the rest below it (CONTRACT §9). Check this with the send-only key on a real inbox early; if `content_id` isn't accepted, fall back to signed Supabase URLs valid for 30 days.

**Supabase Storage**

- **Uploads:** the `storage.from_("files").upload(f"{business_id}/{file_id}", data, {"content-type": mime})`.
- **Downloads:** `download(path)`.
- **Metadata:** a `files` table keeps the `FileRef` rows (RLS on, like every table); `read_file` checks `business_id` there first.
- **Serving:** the dashboard gets files through an authenticated route, `/files/{file_id}`, with thumbnails made by Pillow.

**Scheduler**

- **The loop:** an `asyncio` task started in `main.serve`, ticking every 60 s.
- **Run times:** `zoneinfo.ZoneInfo(cadence.timezone)` gives the next run in local time, stored in UTC.
- **Tracking:** due runs are recorded per `schedule_id` so a slow run isn't started twice. The rules for blocked threads, missed runs and "Run now" are in CONTRACT §5.
