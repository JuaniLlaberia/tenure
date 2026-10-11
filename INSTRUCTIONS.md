# Running and using Tenure

Nothing is hosted, so you run your own copy: one process runs the Telegram bot and the dashboard. This page covers setup, then a walkthrough of the demo. Back to the [README](README.md).

- [Three ways to try it](#three-ways-to-try-it)
- [1. Install](#1-install)
- [2. Keys and settings](#2-keys-and-settings)
- [3. Supabase](#3-supabase)
- [4. Telegram bot and group](#4-telegram-bot-and-group)
- [5. Run it](#5-run-it)
- [6. Using it: the demo walkthrough](#6-using-it-the-demo-walkthrough)
- [7. Commands](#7-commands)
- [8. The brain in the terminal](#8-the-brain-in-the-terminal)
- [9. Tests and lint](#9-tests-and-lint)
- [10. Troubleshooting](#10-troubleshooting)
- [11. Repo layout](#11-repo-layout)

## Three ways to try it

| Way | What you need | What you get |
| --- | --- | --- |
| **A. Brain in the terminal** | An OpenRouter key | The real agent teams in a terminal chat, with fake tools (nothing is posted or sent). See [section 8](#8-the-brain-in-the-terminal). |
| **B. App with the fake brain** | A Telegram bot token | The full Telegram and dashboard experience with scripted drafts, and no models called. Set `FAKE_BRAIN=1`. |
| **C. Full setup** | All of the above, plus Supabase, and optionally Bluesky and Resend | Everything: real teams, real posts and emails, data that survives restarts. |

## 1. Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs the right Python (3.12) for the project, and a Telegram account.

```bash
git clone https://github.com/JuaniLlaberia/tenure.git
cd tenure
uv sync
cp .env.example .env
```

## 2. Keys and settings

Fill in `.env`. Only what your chosen way needs is required. Everything else is optional, and the app says in its log what's off.

| Variable | Needed for | Where to get it | Without it |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | A, C | [openrouter.ai/keys](https://openrouter.ai/keys) | The brain can't run (use `FAKE_BRAIN=1`) |
| `TELEGRAM_BOT_TOKEN` | B, C | [@BotFather](https://t.me/BotFather) in Telegram, see [section 4](#4-telegram-bot-and-group) | The app won't start |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | C | Supabase project → **Project Settings → API**: the project URL and the `service_role` (secret) key | Data is kept in memory and lost on restart |
| `DATABASE_URL` | C | Supabase → **Connect** → the **Session pooler** connection string (not the transaction pooler) | Brain conversations are lost on restart |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Posting | [bsky.app](https://bsky.app) → **Settings → Privacy and security → App passwords**. Use a test account. | Approved posts fail with "Bluesky isn't connected" |
| `RESEND_API_KEY`, `RESEND_FROM` | Newsletters | [resend.com](https://resend.com) → API Keys. Set `RESEND_FROM=onboarding@resend.dev` (see below) | Approved emails fail with "Email isn't connected" |
| `KEENABLE_API_KEY` | Research | Keenable | Search falls back to DuckDuckGo |
| `FAKE_BRAIN` | B | Set to `1` | The real brain is used |

**Resend's test sender.** `onboarding@resend.dev` works without verifying a domain, but Resend only delivers it to **the email address of your own Resend account**. When the marketing team asks where newsletters should go, give it that address.

**Models.** The `MODEL_*` defaults in `.env.example` are the ones we demo with, and they all run through your OpenRouter key: DeepSeek for the work, Claude for the check, Gemini for voice notes, photos and images, and Jev for decisions.

**Speed.** `LLM_REASONING=true` gives better judgement but can take up to about 20 seconds per call. Set it to `false` for snappier replies (about 3–4 seconds) while you try things out.

**The other settings** (`LLM_TIMEOUT`, `DECIDE_THRESHOLD`, `ROUTE_THRESHOLD`, `OPENROUTER_PROVIDER`, `PORT`, `DASHBOARD_HOST`, `DASHBOARD_URL`, `LOG_LEVEL`) can keep their defaults.

## 3. Supabase

Skip this section for ways A and B.

1. Create a project at [supabase.com](https://supabase.com).
2. Open the **SQL editor**, paste [`src/app/store/schema.sql`](src/app/store/schema.sql) and run it. It creates the tables and a private `files` storage bucket, and it's safe to run again.
3. Copy `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `DATABASE_URL` into `.env` (see the table above).

## 4. Telegram bot and group

1. **Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot` and follow the steps. Put the token in `TELEGRAM_BOT_TOKEN`.
2. **Create the company group.** Make a new Telegram group (you can be its only member) and name it after your business.
3. **Turn on Topics.** In the group: **Edit → Topics → on**. Each team gets its own topic, and General is where you talk to the chief of staff.
4. **Add the bot and make it an admin** with the **Manage topics** permission, so it can create a topic for every team you hire.

One group is one business. You can run several groups on the same bot.

## 5. Run it

```bash
uv run --env-file .env python -m app.main
```

The log says which brain and store it uses and which tools are on, then `Bot polling; dashboard on http://localhost:8000`.

**Dashboard.** Send `/dashboard` in the group. The bot replies with a private link and a one-off password. Open the link on the machine that runs the app. To open it from another device, expose the port (with a tunnel such as `cloudflared` or `ngrok`, for example), then set `DASHBOARD_HOST=0.0.0.0` and `DASHBOARD_URL` to the public address. `/dashboard_stop` turns it off.

## 6. Using it: the demo walkthrough

Everything works from Telegram alone. The dashboard is an organized view of the same things.

1. **Set up the business.** In the group's **General** topic, send `/start`. Alex, your chief of staff, interviews you about what you sell, your customers, prices and tone. Paste your website or send a PDF (a price list, a brand guide) to fill in answers faster. When the profile is complete, Alex offers the teams to hire.
2. **Hire Marketing.** Tap **Marketing** or send `/hire marketing`. A new topic opens, and Maya, the marketing lead, asks three questions: which channels you post on, any launch coming up, and where newsletters should go (your Resend account's email).
3. **Ask for work.** In the Marketing topic: *"We launch Friday, get the word out."* You can also send it as a voice note. Maya splits it into tasks (for example a Bluesky post and a newsletter), shows progress while the specialists work, and the independent check reviews each draft before you see it.
4. **Edit a draft.** Tap **Edit** on an approval card, pick what to change if it asks (text, subject, recipient, an image), and send your version, for example without the hashtags. Maya says what she learned ("Got it, no hashtags from now on"), and the next drafts follow it. `/knowledge` lists every lesson, with **Forget**.
5. **Approve.** Tap **Approve** on the post, and it goes live on Bluesky with a link. **Undo** removes it within 10 minutes (`/activity` shows everything that went out). Emails can't be unsent, so they have no undo.
6. **Reject with a reason.** Tap **Reject**, then **Give a reason** and say why. The team revises the draft, and the reason becomes a lesson too.
7. **Earn autonomy.** After a streak of clean approvals (no edits) on one task type, Maya offers to post without asking. Accept it, and take it back anytime with `/team`. **Tip:** the default streak is 5. To see an offer sooner, open `/team` in the Marketing topic and lower **Offer after** with **−**.
8. **Hire Design.** `/hire design` brings Iris, who asks about brand colours, visual style and anything never to show. In the Design topic: *"Make an image for our Friday launch."* Feedback like "warmer colours, no people" becomes a lesson. Once Design is hired, every marketing post and newsletter comes with an image made for it.
9. **Send a photo.** In the Marketing topic, send a photo with the caption *"Post this with a line about our new studio."* The photo goes on the draft.
10. **Schedules.** *"Every Monday morning, post a tip for the week."* The team saves a schedule (it may ask your timezone first). `/schedules` lists them, with **Run now** to see one work without waiting.
11. **Spend.** `/spend` shows what the models cost this week.

`/stop` stops what the team in the current topic is working on, and `/cancel` stops an edit or a reason you started.

## 7. Commands

In a team's topic, the reports show that team. In General, they show the whole business.

| Command | What it does |
| --- | --- |
| `/start` | Set up your business (in the company group) |
| `/hire` | Hire a team, e.g. `/hire marketing` or `/hire design` |
| `/drafts` | Everything waiting for your OK |
| `/team` | Trust per task type: lower it, or change when the team asks for more |
| `/schedules` | Repeating work: run now, stop or turn back on |
| `/knowledge` | What the team knows about you, with Forget |
| `/activity` | What went out, with Undo, and this week's tasks |
| `/spend` | What the models cost this week |
| `/dashboard` | Get the dashboard link and a new password |
| `/dashboard_stop` | Turn the dashboard off |
| `/cancel` | Stop an edit or a reason you started |
| `/stop` | Stop what the team in this topic is working on |
| `/help` | What the bot can do |

## 8. The brain in the terminal

The real brain without Telegram or Supabase: it needs only `OPENROUTER_API_KEY` in `.env`. Tools are faked, so posts and emails are printed instead of sent, and the data lives in memory.

```bash
uv run python -m brain.cli
```

| Command | What it does |
| --- | --- |
| *any text* | Talk in the current topic (General, or the team you switched to) |
| `/start` | Business onboarding with the chief of staff |
| `/hire <template>` | Hire a team (`marketing`, `design`); it becomes the current topic |
| `/team <name>`, `/general` | Switch topic |
| `/file <path>` | Attach a local photo, voice note or PDF to your next message |
| `/approve`, `/edit <text>`, `/reject [reason]` | Answer the latest draft |
| `/accept`, `/decline` | Answer the latest promotion offer |
| `/undo` | Undo the latest post |
| `/run <schedule title>` | Run a schedule now |
| `/seed <task_type> <n>` | **Demo only:** pretend the founder approved n drafts, to reach a promotion offer quickly |
| `/lessons`, `/tasks`, `/log` | Show what the store holds |
| `/help`, `/quit` | |

## 9. Tests and lint

```bash
uv run pytest               # the suite; no keys or network needed
uv run pytest -m live       # tests that call real OpenRouter models (needs OPENROUTER_API_KEY)
uv run ruff check . --fix   # lint and sort imports
```

We don't use `ruff format`: it forces two blank lines between definitions, and our style uses one. CI (`.github/workflows/ci.yml`) runs `uv sync --locked`, `ruff check` and `pytest` on every push and pull request to `main` and `dev`.

## 10. Troubleshooting

| You see | Fix |
| --- | --- |
| `TELEGRAM_BOT_TOKEN is not set` | Add the token to `.env`, and start the app with `--env-file .env` |
| `OPENROUTER_API_KEY is not set, so the brain can't run` | Add the key, or set `FAKE_BRAIN=1` |
| `Couldn't ... after 5 tries` at startup | Check that Supabase is up and that `SUPABASE_*` and `DATABASE_URL` are right. `DATABASE_URL` must be the session pooler, not the transaction pooler. |
| The bot answers in a private chat with "Add me to your company group…" | Talk to it in the group, not in a DM |
| "Turn on Topics in the group settings first" | Group → Edit → Topics → on, then `/start` again |
| "I need to be an admin who can manage topics" | Promote the bot to admin with **Manage topics** |
| "Bluesky isn't connected" / "Email isn't connected" | Set the Bluesky or Resend variables and restart. A bad Bluesky login shows up in the log at startup. |
| The newsletter never arrives | With `onboarding@resend.dev`, Resend only delivers to your own Resend account's email |
| Replies are slow | Set `LLM_REASONING=false` |

## 11. Repo layout

| Folder | Owner | Contains |
| --- | --- | --- |
| `src/contract/` | Both | Shared models and Protocols: what the brain and the app provide each other |
| `src/brain/` | Juan | LangGraph graphs, prompts, team templates (YAML), onboarding, learning, `decide()`, autonomy |
| `src/app/` | Mark | Telegram bot, FastAPI dashboard, Supabase store, tools (Bluesky, email, search, files) |
| `tests/` | Both | `contract/`, `brain/`, `app/` |
| `docs/` | Both | [Context](docs/CONTEXT.md), [contract](docs/CONTRACT.md), [engine spec](docs/specs/brain-engine.md), [roadmap](docs/ROADMAP.md), [brain stages](docs/brain/) |
| `assets/avatars/` | Both | The team personas' pictures |

A new team is a new YAML file in `src/brain/templates/`; see the [engine spec](docs/specs/brain-engine.md). [CLAUDE.md](CLAUDE.md) has the architecture rules we kept to.
