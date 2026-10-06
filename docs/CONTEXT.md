# Tenure: project context

> Working name, not final. Built by Juan (agent brain) and Mark (app, integrations and infra) for the Innovation Intelligence Hackathon, UC Berkeley, SF Tech Week, Oct 5–11, 2026. Track: AI-Native Enterprise.

This file explains what we're building and why. Read it before writing code or prompts, and give it to any AI coding assistant as context. The technical agreement between brain and app lives in [CONTRACT.md](./CONTRACT.md); the design of the team engine lives in [specs/brain-engine.md](./specs/brain-engine.md).

## One-liner

Solo founders hire AI teams for any function. Each team lives in the founder's Telegram, learns the business once, gets better at it with every piece of feedback, and earns more autonomy as the founder approves its work.

**"We don't sell agents. We sell you a team."**

## The problem

- There are over 30 million US businesses with no employees. Their owners are experts at one thing and amateurs at everything else: marketing, invoicing, admin, sales.
- Hiring people or freelancers is expensive. AI tools exist, but owners don't trust them to act on their behalf: only 6% highly trust AI with their brand voice or customers.
- Today's AI products sit at two extremes: tools that only hand you drafts (Sintra-style helpers), and tools that act without asking (Polsia runs your company autonomously). Nothing in between earns trust the way a new employee does.

## The insight

**"The bottleneck isn't what AI can do; it's what owners will let it do."**

Real employees earn trust over time, and they learn how *this* boss likes things done. Ours do both. Every AI team starts on probation: it drafts work and the founder approves it. Every edit and rejection teaches the team something about this business. After a streak of approved work on a task type, the team asks for more autonomy on that task type only. The founder can take autonomy back anytime.

## Target customer

An established solo service business, like a consultant, designer or coach earning $100K+ a year, who has no time or skill for marketing and chasing invoices. Not new AI-built startups, and not teams adopting a work suite.

## How it works

### Onboarding (two kinds)
- **Business onboarding (once per business):** a "chief of staff" interviewer chats with the founder until a fixed checklist (the business profile) is complete. It's a real conversation driven by an LLM, but plain code tracks which checklist fields are still missing, so it always produces the same structured profile. Optional "paste your website" step fetches the site and prefills answers. Starts when the founder sends `/start` in the company group.
- **Team onboarding (per hire):** the new team's lead asks only 2–3 role-specific questions that tune the team to this business ("Which channels do you post on?"). Answers are saved in the team's memory. Everything else comes from the shared business memory, so hiring feels instant.

### Hiring teams
- Founders hire from the dashboard ("Hire a team") or from chat ("/hire marketing").
- One company group in Telegram per business; each team gets its own forum topic, created automatically on hire. The General topic is the company-wide chat with the chief of staff.
- One bot posts as different personas ("**Maya (Marketing lead):** …").
- Many businesses can use the product at the same time; each Telegram group is one business.

### Inside a team
1. The founder writes in the team's topic.
2. The **lead** plans the work: splits the request into 1–3 tasks, each of a known task type, and asks when something is unclear.
3. Each task runs through the **steps** its task type defines (e.g. a newsletter is researcher → writer). A **specialist** (writer, researcher, invoicer) does one step with its own tools, then control returns to the lead.
4. An **independent check** reviews the result against the task type's rules and the team's lessons; the lead never grades its own team's work. Failed checks go back for a revision (up to 2).
5. **Approval:** Approve, Edit or Reject in Telegram, skipped only for task types that have earned autonomy.
6. **Action:** post to Bluesky or send an email. Every action is logged. Posts can be undone for 10 minutes; emails can't be unsent, so they are never undoable.
7. **Learning:** edits, rejections with a reason and feedback in chat become lessons for this team; approved drafts become examples of what this founder likes.

Rules: control always returns to the lead; specialists never talk to each other; they share state (plan, drafts, memory) instead.

### Teams learn your business
Inspired by the learning loop of Hermes-style agents: every hired team keeps its own memory per business. Business A's marketing team and business B's marketing team start from the same template but drift apart as each founder edits, rejects and comments.

- **Sources:** team onboarding answers, edits (original vs. edited text), rejections with a reason, feedback said in chat ("stop using hashtags"), and recently approved drafts as examples.
- **Reflect:** the brain turns each piece of feedback into a short lesson ("No emojis; sign posts as Juan"), and decides whether it applies to this team only or to the whole business ("we never offer discounts" is shared with every team, including ones hired later).
- **Use:** lessons go into the lead, specialist and check prompts, so a draft that breaks a lesson fails the check before the founder sees it.
- **Visible and editable:** the lead confirms what it learned in chat ("Got it, no emojis from now on"), and the dashboard shows "What Maya knows about you", where the founder can delete any lesson.

### Autonomy ladder (per team and task type)
`draft_only` → `act_after_approval` (default) → `act_and_report` → `autonomous`

Five approvals in a row without edits, with high-confidence checks, trigger a promotion offer. Edits, rejections and undos reset the streak. Finance never moves money at any level.

### Decisions vs. work
- **LLMs do the work:** planning, writing, questions, reports, reflecting on feedback.
- **Jev (TypeSafe AI, via OpenRouter) makes the calls:** which task types a request needs, is it clear enough, does the draft pass the check, is an onboarding answer clear. Each decision has a confidence score; below the threshold, the system takes the safe path (asks the founder, revises, or escalates). LLM structured output is the fallback.
- **Plain code makes the rules:** approvals, autonomy, promotions, undo windows, onboarding checklist completeness.

### Teams are data, not code
Every team runs on the same engine. A team is a YAML template: lead, specialists, tools, task types (each with its fixed steps, output, check rules, action and starting autonomy) and onboarding questions. Adding a new team means adding a file; adding a new capability (a tool or output format) is code written once and usable by every team.

## Hackathon scope

| Part | Status |
| --- | --- |
| Business onboarding interview → memory | Must work |
| Marketing team: plan, write, research, check, approve, post to Bluesky, send email | Must work |
| Learning: edits, rejects, chat feedback → lessons; approved examples; visible in chat and dashboard | Must work |
| Approvals, undo, audit log | Must work |
| Autonomy ladder | Simple rule; approval history may be seeded for the demo (and we say so) |
| Dashboard | One page: teams, tasks, approvals, log, lessons, "Hire a team" |
| Finance team (thin): payment reminder from memory, reminder email sent | Stretch: only after marketing works end to end |
| Sales, support, assistant teams | Templates only |
| Out of scope | WhatsApp, Slack, real payments, auth and billing, multi-tenant scaling |

## Stack

Python 3.11+ · uv · LangGraph · OpenRouter (LLMs and Jev; models chosen later) · FastAPI · python-telegram-bot (polling, forum topics) · Supabase (Postgres) · dashboard in Lovable or Streamlit (Mark decides) · Bluesky (atproto) · Resend · Keenable search and fetch (hackathon credits; DuckDuckGo as fallback; Mark decides) · pytest.

## Who owns what

| Person | Area | Folder |
| --- | --- | --- |
| Juan | Agent brain: graphs, prompts, templates, onboarding, learning, decisions, autonomy policy, tests | `src/brain/` |
| Mark | App: Telegram, FastAPI, Supabase, dashboard, integrations, deployment | `src/app/` |
| Both | Shared contract: models and interfaces | `src/contract/`, `docs/` |

## Pricing (to validate)

- Flat price per team, with a monthly capacity instead of credits.
- Free 14-day "probation," then $49 per team per month for about 100 tasks; Pro team $99 for about 300.
- A task is one delivered deliverable (post, newsletter, invoice reminder), with 2 revision rounds included.
- Near the limit, work queues and the lead suggests upgrading. No surprise charges.

## Competitors in one line each

- **Sintra, Marblism:** persona helpers that mostly draft.
- **Motion:** AI employees inside its work suite; you must move your work there.
- **Lindy, Relevance AI:** agent builders; you configure workflows like software.
- **Polsia:** autonomous AI that runs your company; control is the top concern.
- **Artisan, 11x, Intercom Fin:** one role, deep.

**Our line:** "They give you drafts or run your company unsupervised. Ours earns your trust, one task at a time, in your group chat."

## Demo scenario (3 minutes, draft; not decided yet)

1. Hook: 30M businesses with no employees; name a real interviewee.
2. Insight: "The bottleneck isn't what AI can do; it's what owners will let it do."
3. Hire the marketing team; the lead asks three onboarding questions.
4. "We launch Friday, get the word out": the lead splits the work; specialists reply with drafts.
5. Edit a draft: the lead says what it learned. Approve: a real post goes live. Approval history triggers a promotion offer.
6. (Stretch) Hire the finance team: it already knows the client and drafts the reminder.
7. Close: price per team, this week's traction, the ask.

## Key dates

- Wed Oct 7 and Thu Oct 8, 6–8 PM: office hours at SCET (Grimes Engineering Center).
- Thu Oct 8: full build day.
- Sat Oct 10, 11:59 PM: submit repo, demo video, one-paragraph thesis.
- Sun Oct 11, 1–4 PM: Demo Day.

## Glossary

- **Business:** one founder's company; one Telegram group.
- **Team:** a lead plus specialists, defined by a YAML template, hired by one business.
- **Lead:** the team's manager; plans, asks, assigns, combines, reports.
- **Specialist:** does one step with its own tools; generic subgraph configured by YAML.
- **Task:** one deliverable; the unit of capacity and pricing.
- **Task type:** a kind of task (social_post, newsletter, invoice_reminder) with fixed steps; trust is tracked per task type.
- **Steps:** the ordered specialists a task type runs through.
- **Check:** independent review before anything ships.
- **Lesson:** something a team learned about this business (a fact or a preference), scoped to one team or the whole business.
- **Reflect:** the brain step that turns feedback into lessons.
- **Promotion:** moving a task type up the autonomy ladder.
