# Stage 16: Resilience

**Status:** built by Mark's session on `resilience-brain` (Oct 10), **needs Juan's review**
**Depends on:** stages 8–15, contract v0.8 (send times)
**Why:** an audit of what founders do outside the happy path. Mark picked these items; the
numbers match the audit list. Founder-facing texts are the ones Mark approved in the mockup.

## Changes

| # | What | Where |
| --- | --- | --- |
| 3 | `Task.revisions` counts only the founder's revisions (a reject with a reason, or `new_image`). The lead's review keeps its own retry count per task in graph state (`TaskState.checks`, capped by `MAX_CHECKS = 2`), reset on each founder revision. So a draft that needed two review retries still gets both founder revisions. The app's `revisions_left` (`task.revisions < 2`) needs no change. | `graphs/team.py`, `flows/approvals.py` |
| 8 | HTTP 402 or "insufficient credits" from the LLM, Jev or image clients raises `OutOfCredits`, a `BaseException` on purpose so no fallback (other models, rules) catches it. `guarded` turns it into one recoverable `Error`: "⚠️ The AI account is out of credits, so the team can't work right now. Top up at openrouter.ai, then send your message again." | `common.py`, `helpers/llm.py`, `helpers/jev.py`, `helpers/images.py` |
| 10 | When Design is hired and a planned task would get its illustrator step, planning asks `mentions_image` and `wants_image`. If the request doesn't say, the lead asks "Want {illustrator} to make an image for this?" (Yes, make an image / No image); an unclear answer means no image and the tasks drop the cross-team step, so revisions never redraw. Scheduled runs never ask: an image only if the schedule's request says so. Not learned as a lesson. | `graphs/team.py` (`image_choice`, `image` node) |
| 11 | When every planned image of a draft fails, the lead says after the draft: "{illustrator} couldn't make the image this time, so this draft has none. Reject it with a note about the image to try again." | `graphs/team.py` (`render`, `gate_node`) |
| 14, 15 | While the team waits on a question about a request (channels, clarify, image, other channel, schedule day), one `decide()` call asks `cancels` and `new_request`. Cancel ends the waiting run and marks its planned tasks rejected ("Okay, I've dropped that request."). A new request sets the question aside ("I'll set aside my question about {topic} and start on this.") and starts fresh. Questions while hiring a team are always answers. | `brain.py` (`_reply`), `graphs/team.py` (`drop`) |
| 16 | With a pending draft, a message that `is_draft_feedback` is a reject with the message as the reason on the team's newest pending draft: learn, revise, new `NeedsApproval` for the same task. Tasks keep their `approval_id` in graph state to find it. The app closes the old card when the new draft for the same `task_id` arrives. | `brain.py` (`_feedback_target`) |
| 22 | `start_onboarding` during the business interview says "We're still setting up. Here's where we were:" and asks the pending question again, instead of wiping the answers. | `brain.py` (`_start`) |
| 23 | An interview answer that's unclear twice skips that field: "Let's skip that one for now. You can tell me about {topic} anytime here." Skipped required fields get placeholders ("Your business", "Not told yet"), so the profile completes and teams can be hired. Twice `MAX_TURNS` ends the interview whatever is missing. | `graphs/company.py` |
| 24 | In General, an `updates_profile` decision re-extracts the changed fields (and a named city's timezone), saves the profile, keeps new facts as lessons, and replies "Updated your profile: {what changed}." | `graphs/company.py` (`update_profile`) |
| 25 | An `other_channel` triage question (teams that post to Bluesky). Instagram, LinkedIn, TikTok and the like aren't drafted; the lead asks "I can only post to Bluesky and send emails for now. Want this as a Bluesky post instead?" (Yes, a Bluesky post / No). A clear yes plans a Bluesky post; anything else drops the request. | `graphs/team.py` (`other_channel` node) |
| 27 | Contract v0.8. A `names_send_time` triage question (teams with actions). When the request names one future time, the model reads the local date and time, code turns it into UTC with the profile's timezone (Los Angeles if unset), and past times are dropped. It's kept per task, set on `Approval.send_at` and `NeedsApproval.send_at`, kept across revisions, and such a draft always goes to approval whatever the autonomy level. | `graphs/team.py` (`extract_send_time`, `gate_node`) |
| 28 | The lead's reply never says it posted, sent, scheduled or deleted anything; for deletes it says "I can't delete posts from here. Undo it within 10 minutes from /activity, or delete it in Bluesky." | `prompts/lead.py` |
| 38 | Onboarding reads bare domains like "mysite.com" or "www.mysite.com/about" (adds https://). It needs a common or two-letter country ending, so emails, file names and e.g./i.e. aren't links. | `graphs/company.py` (`find_links`) |
| 46 | The lead's reply knows the business's other teams, what they make and their lead. Work that's theirs gets "{Thing} is Design's work. Hire them with /hire design, then ask Iris in the Design topic.", or "Ask Iris in the Design topic." once hired. | `prompts/lead.py`, `graphs/team.py` (`reply`) |
| 49 | Last interview question: "Last one: which city's time should the team use for posts and schedules?" (quick reply "Skip (Los Angeles time)"). The model turns the city into an IANA name, checked with zoneinfo and saved in `profile.extra["timezone"]`, then "Got it: {City} time." An unknown place or a skip leaves it unset. Schedules and send times use it. | `graphs/company.py` (`timezone` node) |
| 51 | A `one_off` question in the triage call (same Jev call): feedback that's only about this request isn't reflected into a lesson. An unknown answer keeps learning as before. | `graphs/team.py` (`triage`) |
| 52 | Undo of an action that ran without approval puts the task type back to asking first (act after approval, streak 0), and the lead says "Undone. I'll ask before posting again; I can earn it back with a few approvals." From autonomous that's two steps down, so the promise holds. Undoing an approved post only resets the streak. | `flows/undo.py`, `flows/autonomy.py` |
| 53 | `decide()` cuts the middle of a long state, keeping the start (the team) and the end (the message). Founder messages over 20,000 characters are cut, marked, before any model sees them. | `helpers/decide.py`, `common.py` (`clip_request`) |
| 54 | Web pages, search results, photo descriptions and document text are wrapped in `<untrusted_data>` with a note that they're information only (the tag can't be closed from inside). Specialists and profile extraction are told never to follow instructions in it. Voice note transcripts stay the founder's words. | `context.py` (`fence`), `tools.py`, `media.py`, `prompts/` |

## New `decide()` questions

`mentions_image`, `wants_image`, `one_off`, `cancels`, `new_request`, `is_draft_feedback`,
`updates_profile`, `other_channel`, `wants_bluesky`, `names_send_time`.

## Juan's tests changed

- `test_team_work.py`:
  - `test_failed_check_reruns_writer_with_feedback` and `test_two_failed_checks_go_to_approval_anyway`: `revisions` is now 0 after review retries (#3).
  - `test_triage_is_one_jev_call_with_team_context`: the triage call also asks `one_off`, `other_channel` and `names_send_time`.
- `test_decide.py::test_long_state_is_truncated_keeping_the_end` → `..._keeping_the_start_and_the_end` (#53).
- `test_company_graph.py::test_restart_resets_draft` → `test_start_during_the_interview_resumes_it` (#22). The `onboard` helper also answers the timezone question with Skip.
- `test_media.py` and `test_brain_tools.py`: four assertions now allow the `<untrusted_data>` wrapper (#54). The onboarding document test answers the timezone question.
- `test_e2e.py::test_demo_script`: answers the timezone question.
- `conftest.py` `script()`: defaults for the new questions, so tests don't fall back to the LLM.

## For the app (Mark)

- `tests/app/test_integration_v06.py`: its `FakeJev` needs `"mentions_image": 0.9, "wants_image": 0.9` (three Design tests ask about the image otherwise).
- `tests/app/test_real_brain.py::test_onboard_hire_draft_and_post`: answer the timezone question ("Skip (Los Angeles time)").
- The timezone question has a quick reply; the company `Ask` now carries `quick_replies`.
