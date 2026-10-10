"""
Turns Telegram input into Brain calls and renders the event streams back into the chat.
"""

import asyncio
import logging
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal
from uuid import uuid4

from app.chat import reports, ui
from app.chat.port import Chat, IncomingFile, Keyboard
from app.chat.state import (
    ActionCard,
    ApprovalCard,
    AppState,
    AskCard,
    Card,
    EditDraft,
    LessonCard,
    OfferCard,
    PasswordMessage,
    Pending,
    ReplaceCard,
    ScheduleCard,
)
from app.files import MAX_DOWNLOAD, Files, avatar_bytes
from app.scheduler import next_run
from app.store.base import AppStore, TelegramTopic
from app.store.memory import InMemoryStore
from app.web import auth
from contract import (
    ActionDone,
    ActionUndone,
    ApprovalDecision,
    Ask,
    AutonomyLevel,
    Brain,
    Error,
    Event,
    FileRef,
    IncomingMessage,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    PlannedAction,
    PostSocial,
    Progress,
    PromotionOffer,
    PromotionResponse,
    Say,
    Schedule,
    ScheduleSaved,
    SendEmail,
    Team,
    TeamHired,
    TemplateInfo,
)

logger = logging.getLogger(__name__)

STATE_KEPT = timedelta(days=14)
EMAIL_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LEVELS = list(AutonomyLevel)
PROMOTE_AFTER_MAX = 20
DASHBOARD_WAIT = 30.0
DASHBOARD_FOOTERS = {
    "approve": ui.APPROVED_ON_DASHBOARD,
    "edit": ui.EDITED_ON_DASHBOARD,
    "reject": ui.REJECTED_ON_DASHBOARD,
    "new_image": ui.NEW_IMAGE_ON_DASHBOARD,
}
REVISIONS_INCLUDED = 2

Events = Callable[[], AsyncIterator[Event]]
Tap = Callable[[int, int, str], Coroutine[Any, Any, str | None]]

class Refused(Exception):
    """
    A dashboard action that can't be done; the message is safe to show.
    """

def _utcnow() -> datetime:
    return datetime.now(UTC)

def _short_id() -> str:
    return uuid4().hex[:12]

@dataclass
class _Buffer:
    texts: list[str]
    message_id: int
    sent_at: datetime
    files: list[FileRef] = field(default_factory=list)
    loading: int = 0
    changes: int = 0

@dataclass
class _View:
    kind: str
    team_id: str | None
    page: int = 0

@dataclass
class _Run:
    chat_id: int
    thread_id: int | None
    status_threads: set[int | None] = field(default_factory=set)

class Flows:
    def __init__(
        self,
        brain: Brain,
        chat: Chat,
        state: AppState | None = None,
        store: AppStore | None = None,
        debounce: float = 2.5,
        typing_every: float = 4.0,
        clock: Callable[[], datetime] = _utcnow,
        dashboard_url: str = "http://localhost:8000",
        password_ttl: float = 600,
    ) -> None:
        self._brain = brain
        self._dashboard_url = dashboard_url.rstrip("/")
        self._deciding: set[str] = set()
        self._hiring: set[tuple[str, str]] = set()
        self._asking: set[str] = set()
        self._running: set[str] = set()
        self._views: dict[tuple[int, int], _View] = {}
        self._password_ttl = password_ttl
        self._timers: set[asyncio.Task] = set()
        self._chat = chat
        self._state = state or AppState()
        self._store = store or InMemoryStore()
        self._state.journal.store = self._store
        self._files = Files(self._store, clock)
        self._debounce = debounce
        self._typing_every = typing_every
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._buffers: dict[str, _Buffer] = {}
        self._tasks: set[asyncio.Task] = set()
        self._taps: dict[str, Tap] = {
            ui.APPROVE: self._tap_approve,
            ui.EDIT: self._tap_edit,
            ui.REJECT: self._tap_reject,
            ui.REASON: self._tap_reason,
            ui.DROP: self._tap_drop,
            ui.UNDO: self._tap_undo,
            ui.ANSWER: self._tap_answer,
            ui.HIRE: self._tap_hire,
            ui.PROMOTE_YES: self._tap_promote_yes,
            ui.PROMOTE_NO: self._tap_promote_no,
            ui.FORGET: self._tap_forget,
            ui.RUN_NOW: self._tap_run_now,
            reports.LOWER: self._tap_lower,
            reports.LOWER_YES: self._tap_lower_yes,
            reports.LOWER_NO: self._tap_lower_no,
            reports.THRESHOLD: self._tap_threshold,
            reports.PAGE_TO: self._tap_page,
            ui.NEW_IMAGE: self._tap_new_image,
            ui.IMAGE_REASON: self._tap_image_reason,
            ui.IMAGE_AGAIN: self._tap_image_again,
            ui.EDIT_FIELD: self._tap_edit_field,
            ui.EDIT_APPROVE: self._tap_edit_approve,
            ui.EDIT_MORE: self._tap_edit_more,
            ui.EDIT_UNDO: self._tap_edit_undo,
            ui.STOP: self._tap_stop,
            ui.TURN_ON: self._tap_turn_on,
            ui.REPLACE: self._tap_replace,
            ui.KEEP: self._tap_keep,
        }

    async def drain(self) -> None:
        """
        Waits until every brain call started so far has finished (tests and shutdown).
        """
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self._state.journal.flush()

    async def load(self) -> None:
        """
        Restores what the bot knows after a restart: groups, team topics, the cards it can
        still edit, pending prompts and password messages. Leftover status lines are deleted.
        """
        self._state.businesses.update(await self._store.list_businesses())
        for topic in await self._store.list_topics():
            self._state.add_team(topic.chat_id, topic.thread_id, topic.team_id, topic.name)
        await self._store.prune_state(self._clock() - STATE_KEPT)
        self._state.restore(await self._store.list_state())
        for (chat_id, _), message_id in list(self._state.status.items()):
            try:
                await self._chat.delete(chat_id, message_id)
            except Exception:
                logger.debug("Deleting a leftover status message failed", exc_info=True)
        self._state.status.clear_all()
        for chat_id, message in list(self._state.passwords.items()):
            self._schedule_password_deletion(chat_id, message)

    async def on_start(self, chat_id: int, thread_id: int | None, is_forum: bool) -> None:
        if not is_forum:
            await self._chat.send(chat_id, thread_id, ui.NEEDS_TOPICS)
            return
        business_id = self._state.businesses.get(chat_id)
        if business_id is None:
            business_id = await self._store.create_business(chat_id)
            self._state.businesses[chat_id] = business_id
        self._spawn(chat_id, None, None, lambda: self._brain.start_onboarding(business_id))

    async def on_hire(self, chat_id: int, thread_id: int | None, name: str | None) -> None:
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        if name:
            outcome = await self._request_hire(chat_id, thread_id, business_id, name)
            if outcome == "busy":
                await self._chat.send(chat_id, thread_id, ui.ALREADY_HIRING)
            return
        await self._send_hire_card(chat_id, thread_id, ui.PICK_TEAM)

    async def on_cancel(self, chat_id: int, thread_id: int | None) -> None:
        pending = self._state.pending.pop((chat_id, thread_id), None)
        card = self._state.approvals.get(pending.approval_id) if pending else None
        if card is None:
            await self._chat.send(chat_id, thread_id, ui.NOTHING_TO_CANCEL)
            return
        await self._drop_edit(card.approval_id, None)
        self._state.handled.discard((card.chat_id, card.message_id))
        await self._chat.edit(card.chat_id, card.message_id, card.text, card.keyboard)
        await self._chat.send(chat_id, thread_id, ui.CANCELLED)

    async def on_report(self, chat_id: int, thread_id: int | None, kind: str) -> None:
        """
        /drafts, /team, /schedules, /knowledge, /activity, /spend: the dashboard's tabs in
        the chat, for this topic's team or, in General, the whole business.
        """
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        team_id = self._state.topics.get((chat_id, thread_id)) if thread_id is not None else None
        if kind == "team":
            teams = await self._store.list_teams(business_id)
            teams = [t for t in teams if team_id in (None, t.team_id)]
            if not teams:
                await self._chat.send(chat_id, thread_id, reports.NO_TEAMS)
            for team in teams:
                await self._send_view(chat_id, thread_id, business_id, _View("team", team.team_id))
            return
        await self._send_view(chat_id, thread_id, business_id, _View(kind, team_id))

    async def _send_view(
        self, chat_id: int, thread_id: int | None, business_id: str, view: _View
    ) -> None:
        text, keyboard = await self._report(chat_id, business_id, view)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._views[(chat_id, message_id)] = view

    async def _rerender(self, chat_id: int, message_id: int) -> None:
        view = self._views.get((chat_id, message_id))
        business_id = self._state.businesses.get(chat_id)
        if view is None or business_id is None:
            return
        text, keyboard = await self._report(chat_id, business_id, view)
        try:
            await self._chat.edit(chat_id, message_id, text, keyboard)
        except Exception:
            logger.debug("Updating a list failed", exc_info=True)

    async def _report(
        self, chat_id: int, business_id: str, view: _View
    ) -> tuple[str, Keyboard]:
        all_teams = await self._store.list_teams(business_id)
        names = {team.team_id: team.display_name for team in all_teams}
        mine = view.team_id

        def ours(team_id: str | None) -> bool:
            return mine is None or team_id == mine

        week = self._clock() - timedelta(days=7)
        if view.kind == "drafts":
            pending = await self._store.list_approvals(business_id, "pending")
            busy = self._deciding
            pending = [a for a in pending if ours(a.team_id) and a.approval_id not in busy]
            tasks = {a.task_id: await self._store.get_task(a.task_id) for a in pending}
            links = {
                a.approval_id: ui.message_link(card.chat_id, card.thread_id, card.message_id)
                for a in pending
                if (card := self._state.approvals.get(a.approval_id)) is not None
            }
            shown = {k: v for k, v in tasks.items() if v is not None}
            return reports.drafts_view(pending, shown, links, names if mine is None else {})
        if view.kind == "team":
            team = next((t for t in all_teams if t.team_id == mine), None)
            if team is None:
                return reports.NO_TEAMS, []
            template = self._template(team.template)
            people = [p.name for p in template.personas] if template else []
            return reports.team_view(team, people, await self._store.list_trust(team.team_id))
        if view.kind == "schedules":
            schedules = await self._store.list_schedules(business_id, mine)
            return reports.schedules_view(schedules, names, self.running_schedules)
        if view.kind == "knowledge":
            lessons = await self._store.list_all_lessons(business_id)
            lessons = [x for x in lessons if x.team_id is None or ours(x.team_id)]
            scope = names.get(mine, "your team") if mine else "your teams"
            return reports.knowledge_view(lessons, names, scope, view.page)
        if view.kind == "activity":
            actions = [a for a in await self._store.list_actions(business_id) if ours(a.team_id)]
            tasks = [
                t
                for t in await self._store.list_tasks(business_id, limit=200)
                if ours(t.team_id) and t.updated_at >= week
            ]
            return reports.activity_view(actions, tasks, self._clock(), view.page)
        usage = await self._store.list_usage(business_id, week)
        return reports.spend_view([u for u in usage if ours(u.team_id)]), []

    async def on_help(self, chat_id: int, thread_id: int | None) -> None:
        await self._chat.send(chat_id, thread_id, ui.HELP)

    async def on_dashboard(self, chat_id: int, thread_id: int | None) -> None:
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        token = await self._store.dashboard_token(business_id) or auth.new_token()
        password = auth.new_password()
        password_hash = await asyncio.to_thread(auth.hash_password, password)
        await self._store.set_dashboard(business_id, token, password_hash)
        await self._delete_password_message(chat_id)
        url = f"{self._dashboard_url}/b/{token}"
        minutes = max(1, round(self._password_ttl / 60))
        try:
            message_id = await self._chat.send(
                chat_id,
                thread_id,
                ui.dashboard_text(password, minutes),
                ui.dashboard_keyboard(password, url),
            )
        except Exception:
            logger.info("Telegram refused the dashboard link button; sending the link as text")
            message_id = await self._chat.send(
                chat_id,
                thread_id,
                ui.dashboard_text(password, minutes, url=url),
                ui.dashboard_keyboard(password),
            )
        message = PasswordMessage(
            message_id=message_id,
            delete_at=self._clock() + timedelta(seconds=self._password_ttl),
        )
        self._state.passwords[chat_id] = message
        self._schedule_password_deletion(chat_id, message)

    def _schedule_password_deletion(self, chat_id: int, message: PasswordMessage) -> None:
        delay = max(0.0, (message.delete_at - self._clock()).total_seconds())
        timer = asyncio.create_task(self._expire_password_message(chat_id, message, delay))
        self._timers.add(timer)
        timer.add_done_callback(self._timers.discard)

    async def on_dashboard_stop(self, chat_id: int, thread_id: int | None) -> None:
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        await self._store.set_dashboard(business_id, None, None)
        await self._delete_password_message(chat_id)
        await self._chat.send(chat_id, thread_id, ui.DASHBOARD_STOPPED)

    async def _expire_password_message(
        self, chat_id: int, message: PasswordMessage, delay: float
    ) -> None:
        await asyncio.sleep(delay)
        if self._state.passwords.get(chat_id) == message:
            await self._delete_password_message(chat_id)

    async def _delete_password_message(self, chat_id: int) -> None:
        message = self._state.passwords.pop(chat_id, None)
        if message is None:
            return
        try:
            await self._chat.delete(chat_id, message.message_id)
        except Exception:
            logger.debug("Deleting the password message failed", exc_info=True)

    async def hire_from_dashboard(self, business_id: str, template: str) -> None:
        chat_id = self._chat_for(business_id)
        info = self._template(template)
        if info is None:
            raise Refused(f"There's no '{template}' team to hire.")
        if await self._hired(business_id, template):
            raise Refused(
                f"You already have a {info.display_name} team. To start over, send "
                f"/hire {template} in Telegram and choose Replace team."
            )
        if not self._start_hire(chat_id, None, business_id, template, []):
            raise Refused(ui.ALREADY_HIRING)

    def _template(self, name: str) -> TemplateInfo | None:
        return next((t for t in self._brain.list_templates() if t.name == name), None)

    async def _hired(self, business_id: str, template: str) -> list[Team]:
        teams = await self._store.list_teams(business_id)
        return [team for team in teams if team.template == template]

    async def _request_hire(
        self, chat_id: int, thread_id: int | None, business_id: str, name: str
    ) -> str:
        """
        Hires, or asks to replace a team the business already has. Returns
        "hiring", "asked" or "busy".
        """
        template = name.strip().lower()
        info = self._template(template)
        if info is not None and await self._hired(business_id, template):
            await self._send_replace_card(chat_id, thread_id, business_id, info)
            return "asked"
        if not self._start_hire(chat_id, thread_id, business_id, template, []):
            return "busy"
        return "hiring"

    def _start_hire(
        self,
        chat_id: int,
        thread_id: int | None,
        business_id: str,
        template: str,
        replace: list[Team],
    ) -> bool:
        key = (business_id, template)
        if key in self._hiring:
            return False
        self._hiring.add(key)
        self._track(self._hire(chat_id, thread_id, business_id, template, replace))
        return True

    async def _hire(
        self,
        chat_id: int,
        thread_id: int | None,
        business_id: str,
        template: str,
        replace: list[Team],
    ) -> None:
        try:
            for team in replace:
                await self._fire(chat_id, business_id, team.team_id)
            hire = partial(self._brain.hire_team, business_id, template)
            await self._call(chat_id, None, thread_id, hire)
        finally:
            self._hiring.discard((business_id, template))

    async def _fire(self, chat_id: int, business_id: str, team_id: str) -> None:
        async with self._locks[self._key(business_id, team_id)]:
            thread_id = self._state.team_threads.pop(team_id, None)
            self._state.team_names.pop(team_id, None)
            if thread_id is not None:
                self._state.topics.pop((chat_id, thread_id), None)
                self._state.pending.pop((chat_id, thread_id), None)
                self._state.status.pop((chat_id, thread_id), None)
                try:
                    await self._chat.delete_topic(chat_id, thread_id)
                except Exception:
                    logger.warning("Deleting the old team's topic failed", exc_info=True)
            await self._store.delete_team(team_id)

    async def _send_replace_card(
        self, chat_id: int, thread_id: int | None, business_id: str, info: TemplateInfo
    ) -> None:
        key = _short_id()
        text = ui.replace_text(info)
        keyboard = ui.replace_keyboard(key)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.replacements[key] = ReplaceCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, template=info.name, name=info.display_name,
        )

    async def decide_from_dashboard(
        self,
        business_id: str,
        approval_id: str,
        decision: Literal["approve", "edit", "reject", "new_image"],
        reason: str | None = None,
        edited_text: str | None = None,
        edited_action: PlannedAction | None = None,
    ) -> None:
        """
        Approve, edit or reject a draft from the dashboard, as if done in Telegram. An approval
        or edit returns once it has gone out (or after DASHBOARD_WAIT), so the page reloads with
        the result; a rejection returns at once, since a revision can take minutes.
        """
        approval = await self._store.get_approval(approval_id)
        if approval is None or approval.business_id != business_id:
            raise Refused("I can't find that draft.")
        card = self._state.approvals.get(approval_id)
        prompts = [w for w, p in self._state.pending.items() if p.approval_id == approval_id]
        handled = card is not None and (card.chat_id, card.message_id) in self._state.handled
        busy = handled and not prompts and approval_id not in self._state.edits
        if approval.status != "pending" or busy or approval_id in self._deciding:
            raise Refused("That draft is already being handled.")
        if decision == "new_image" and not await self.revisions_left(approval.task_id):
            raise Refused(ui.NO_REVISIONS_LEFT)
        chat_id = self._chat_for(business_id)
        self._deciding.add(approval_id)
        for where in prompts:
            del self._state.pending[where]
        await self._drop_edit(approval_id, DASHBOARD_FOOTERS[decision])
        if card is not None:
            await self._close(card, DASHBOARD_FOOTERS[decision])
        resolution = ApprovalDecision(
            business_id=business_id,
            approval_id=approval_id,
            decision=decision,
            reason=reason.strip() if reason and reason.strip() else None,
            edited_text=edited_text,
            edited_action=edited_action,
        )
        task = self._track(self._decide_and_release(chat_id, approval.team_id, resolution))
        if decision in ("approve", "edit"):
            await asyncio.wait({task}, timeout=DASHBOARD_WAIT)

    @property
    def deciding(self) -> frozenset[str]:
        """
        Drafts being decided right now, which the dashboard no longer offers.
        """
        return frozenset(self._deciding)

    async def undo_from_dashboard(self, business_id: str, action_id: str) -> None:
        entry = await self._store.get_action(action_id)
        if entry is None or entry.business_id != business_id:
            raise Refused("I can't find that action.")
        if entry.undone_at is not None:
            raise Refused("That was already undone.")
        if entry.undo_until is None or self._clock() > entry.undo_until:
            raise Refused(ui.UNDO_CLOSED)
        chat_id = self._chat_for(business_id)
        card = self._state.actions.get(action_id)
        if card is not None:
            if (card.chat_id, card.message_id) in self._state.handled:
                raise Refused("That's already being undone.")
            await self._close(card, None)
        undo = partial(self._brain.undo_action, business_id, action_id)
        task = self._spawn(chat_id, entry.team_id, self._thread_for(entry.team_id), undo)
        await asyncio.wait({task}, timeout=DASHBOARD_WAIT)

    async def _decide_and_release(
        self, chat_id: int, team_id: str, decision: ApprovalDecision
    ) -> None:
        try:
            thread_id = self._thread_for(team_id)
            await self._call(
                chat_id, team_id, thread_id, lambda: self._brain.resolve_approval(decision)
            )
        finally:
            self._deciding.discard(decision.approval_id)

    @property
    def running_schedules(self) -> frozenset[str]:
        return frozenset(self._running)

    def team_busy(self, team_id: str) -> bool:
        """
        The team is waiting on the founder: an open question, or an edit or reason prompt.
        """
        thread_id = self._state.team_threads.get(team_id)
        waiting = any(thread == thread_id for (_, thread) in self._state.pending)
        asking = any(key.endswith(f":{team_id}") for key in self._asking)
        return waiting or asking

    def start_schedule(self, schedule: Schedule) -> asyncio.Task:
        """
        Runs a schedule now through the team's queue; its card shows "Running now" meanwhile.
        """
        chat_id = self._chat_for(schedule.business_id)
        return self._track(self._run_schedule(chat_id, schedule))

    async def _run_schedule(self, chat_id: int, schedule: Schedule) -> None:
        self._running.add(schedule.schedule_id)
        await self._refresh_schedule(schedule)
        try:
            run = partial(self._brain.run_schedule, schedule.business_id, schedule.schedule_id)
            await self._call(chat_id, schedule.team_id, self._thread_for(schedule.team_id), run)
        finally:
            self._running.discard(schedule.schedule_id)
            latest = await self._store.get_schedule(schedule.schedule_id)
            await self._refresh_schedule(latest or schedule)

    async def _own_schedule(self, business_id: str, schedule_id: str) -> Schedule:
        schedule = await self._store.get_schedule(schedule_id)
        if schedule is None or schedule.business_id != business_id:
            raise Refused("I can't find that schedule.")
        return schedule

    async def run_schedule_now(self, business_id: str, schedule_id: str) -> None:
        """
        "Run now" from Telegram or the dashboard. Doesn't move the next scheduled run.
        """
        schedule = await self._own_schedule(business_id, schedule_id)
        if not schedule.active:
            raise Refused("That schedule is stopped. Turn it back on first.")
        if schedule_id in self._running:
            raise Refused("It's already running.")
        if self.team_busy(schedule.team_id):
            raise Refused(ui.TEAM_BUSY)
        schedule = schedule.model_copy(update={"last_run_at": self._clock()})
        await self._store.save_schedule(schedule)
        self.start_schedule(schedule)

    async def set_schedule_active(self, business_id: str, schedule_id: str, active: bool) -> None:
        """
        Stop or turn back on, from Telegram or the dashboard. Turning on schedules the next run.
        """
        schedule = await self._own_schedule(business_id, schedule_id)
        update: dict[str, Any] = {"active": active}
        if active:
            update["next_run_at"] = next_run(schedule.cadence, self._clock())
        schedule = schedule.model_copy(update=update)
        await self._store.save_schedule(schedule)
        await self._refresh_schedule(schedule)

    async def lower_trust(self, business_id: str, team_id: str, task_type: str) -> AutonomyLevel:
        """
        One step down the ladder and the streak back to 0, from Telegram or the dashboard.
        """
        team = await self._store.get_team(team_id)
        trust = await self._store.get_trust(team_id, task_type)
        if team is None or team.business_id != business_id or trust is None:
            raise Refused("I can't find that team.")
        index = LEVELS.index(trust.level)
        if index == 0:
            raise Refused("This is already the lowest level.")
        update = {"level": LEVELS[index - 1], "approval_streak": 0, "updated_at": self._clock()}
        await self._store.set_trust(trust.model_copy(update=update))
        return LEVELS[index - 1]

    async def set_threshold(self, business_id: str, team_id: str, promote_after: int) -> None:
        """
        How many clean approvals earn a promotion offer, for every task type of the team.
        """
        team = await self._store.get_team(team_id)
        if team is None or team.business_id != business_id:
            raise Refused("I can't find that team.")
        if not 1 <= promote_after <= PROMOTE_AFTER_MAX:
            raise Refused(f"Pick a number from 1 to {PROMOTE_AFTER_MAX}.")
        for trust in await self._store.list_trust(team_id):
            update = {"promote_after": promote_after, "updated_at": self._clock()}
            await self._store.set_trust(trust.model_copy(update=update))

    async def forget_lesson(self, business_id: str, lesson_id: str) -> None:
        found = False
        for lesson in await self._store.list_all_lessons(business_id):
            if lesson.lesson_id == lesson_id:
                await self._store.save_lesson(lesson.model_copy(update={"active": False}))
                found = True
        card = self._state.lessons.pop(lesson_id, None)
        if card is not None:
            await self._close(card, ui.forgotten(card.persona))
        if not found and card is None:
            raise Refused("I can't find that lesson.")

    def _chat_for(self, business_id: str) -> int:
        for chat_id, known in self._state.businesses.items():
            if known == business_id:
                return chat_id
        raise Refused("This business isn't linked to a Telegram group.")

    async def on_text(
        self, chat_id: int, thread_id: int | None, text: str, message_id: int, sent_at: datetime
    ) -> None:
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        team_id = None
        if thread_id is not None:
            team_id = self._state.topics.get((chat_id, thread_id))
            if team_id is None:
                await self._chat.send(chat_id, thread_id, ui.NOT_A_TEAM)
                return
        pending = self._state.pending.get((chat_id, thread_id))
        if pending is not None:
            await self._answer_pending(chat_id, thread_id, pending, text)
            return
        await self._close_asks(chat_id, thread_id)
        self._buffer(chat_id, thread_id, business_id, team_id, text, message_id, sent_at)

    async def on_file(
        self,
        chat_id: int,
        thread_id: int | None,
        caption: str,
        file: IncomingFile,
        message_id: int,
        sent_at: datetime,
    ) -> None:
        """
        A photo, voice note or other file: stored, then joined to the debounced message like
        text. The flush waits for downloads still running.
        """
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        team_id = None
        if thread_id is not None:
            team_id = self._state.topics.get((chat_id, thread_id))
            if team_id is None:
                await self._chat.send(chat_id, thread_id, ui.NOT_A_TEAM)
                return
        if file.size is not None and file.size > MAX_DOWNLOAD:
            await self._chat.send(chat_id, thread_id, ui.too_big(file.size, MAX_DOWNLOAD))
            return
        if (chat_id, thread_id) in self._state.pending:
            await self._chat.send(chat_id, thread_id, ui.FILE_DURING_EDIT)
            return
        await self._close_asks(chat_id, thread_id)
        buffer = self._buffer(
            chat_id, thread_id, business_id, team_id, caption, message_id, sent_at
        )
        buffer.loading += 1
        try:
            data = await self._chat.download(file.telegram_id)
            ref = await self._files.save(
                business_id, data, file.mime_type, name=file.name, source="founder"
            )
            buffer.files.append(ref)
        except Exception:
            logger.exception("Storing a file from Telegram failed")
            await self._chat.send(chat_id, thread_id, ui.FILE_FAILED)
        finally:
            buffer.loading -= 1
            buffer.changes += 1

    async def on_callback(self, chat_id: int, message_id: int, data: str) -> str | None:
        """
        Handles a button tap. Returns a short toast for Telegram, or None.
        """
        if (chat_id, message_id) in self._state.handled:
            return ui.ALREADY_HANDLED
        kind, _, arg = data.partition(":")
        tap = self._taps.get(kind)
        if tap is None:
            return None
        return await tap(chat_id, message_id, arg)

    async def _business(self, chat_id: int, thread_id: int | None) -> str | None:
        business_id = self._state.businesses.get(chat_id)
        if business_id is None:
            await self._chat.send(chat_id, thread_id, ui.NOT_SET_UP)
        return business_id

    def _buffer(
        self,
        chat_id: int,
        thread_id: int | None,
        business_id: str,
        team_id: str | None,
        text: str,
        message_id: int,
        sent_at: datetime,
    ) -> _Buffer:
        key = self._key(business_id, team_id)
        buffer = self._buffers.get(key)
        if buffer is not None:
            buffer.texts.append(text)
            buffer.message_id = message_id
            buffer.sent_at = sent_at
            buffer.changes += 1
            return buffer
        buffer = self._buffers[key] = _Buffer([text], message_id, sent_at)
        self._track(self._flush_later(key, chat_id, thread_id, business_id, team_id))
        return buffer

    async def _flush_later(
        self,
        key: str,
        chat_id: int,
        thread_id: int | None,
        business_id: str,
        team_id: str | None,
    ) -> None:
        buffer = self._buffers[key]
        while True:
            seen = buffer.changes
            await asyncio.sleep(self._debounce)
            if buffer.changes == seen and not buffer.loading:
                break
        self._buffers.pop(key)
        self._asking.discard(key)
        text = "\n".join(part for part in buffer.texts if part)
        if not text and not buffer.files:
            return
        message = IncomingMessage(
            business_id=business_id,
            team_id=team_id,
            text=text,
            message_id=str(buffer.message_id),
            sent_at=buffer.sent_at,
            attachments=buffer.files,
        )
        await self._call(chat_id, team_id, thread_id, lambda: self._brain.handle_message(message))

    def _key(self, business_id: str, team_id: str | None) -> str:
        return f"{business_id}:{team_id or 'company'}"

    def _track(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _spawn(
        self, chat_id: int, team_id: str | None, thread_id: int | None, events: Events
    ) -> asyncio.Task:
        return self._track(self._call(chat_id, team_id, thread_id, events))

    async def _call(
        self, chat_id: int, team_id: str | None, thread_id: int | None, events: Events
    ) -> None:
        business_id = self._state.businesses[chat_id]
        run = _Run(chat_id, thread_id)
        async with self._locks[self._key(business_id, team_id)]:
            typing = await self._start_typing(chat_id, thread_id)
            failed = False
            try:
                async for event in events():
                    try:
                        await self._render(run, event)
                    except Exception:
                        logger.exception("Rendering %s failed", event.type)
                        failed = True
            except Exception:
                logger.exception("Brain call failed")
                failed = True
            finally:
                typing.cancel()
                await self._clear_status(run)
            if failed:
                await self._safe_send(chat_id, thread_id, ui.BROKEN)

    async def _keep_typing(self, chat_id: int, thread_id: int | None) -> None:
        while True:
            try:
                await self._chat.typing(chat_id, thread_id)
            except Exception:
                logger.debug("Typing action failed", exc_info=True)
            await asyncio.sleep(self._typing_every)

    async def _start_typing(self, chat_id: int, thread_id: int | None) -> asyncio.Task:
        typing = asyncio.create_task(self._keep_typing(chat_id, thread_id))
        await asyncio.sleep(0)
        return typing

    async def _clear_status(self, run: _Run) -> None:
        for thread_id in run.status_threads:
            message_id = self._state.status.pop((run.chat_id, thread_id), None)
            if message_id is not None:
                try:
                    await self._chat.delete(run.chat_id, message_id)
                except Exception:
                    logger.debug("Deleting status failed", exc_info=True)

    async def _safe_send(self, chat_id: int, thread_id: int | None, text: str) -> None:
        try:
            await self._chat.send(chat_id, thread_id, text)
        except Exception:
            logger.exception("Sending to Telegram failed")

    def _thread_for(self, team_id: str | None) -> int | None:
        if team_id is None:
            return None
        return self._state.team_threads.get(team_id)

    async def _render(self, run: _Run, event: Event) -> None:
        chat_id = run.chat_id
        business_id = self._state.businesses[chat_id]
        match event:
            case Say():
                thread_id = self._thread_for(event.team_id)
                await self._send_with_images(
                    chat_id, thread_id, business_id, event.media, ui.say_text(event)
                )
            case Progress():
                await self._show_status(run, self._thread_for(event.team_id), ui.status_text(event))
            case Ask():
                await self._render_ask(chat_id, business_id, event)
            case NeedsApproval():
                await self._render_approval(chat_id, business_id, event)
            case ActionDone():
                await self._render_action(chat_id, business_id, event)
            case ActionUndone():
                await self._render_undone(chat_id, event)
            case PromotionOffer():
                await self._render_offer(chat_id, business_id, event)
            case LessonLearned():
                await self._render_lesson(chat_id, business_id, event)
            case TeamHired():
                await self._render_hired(chat_id, business_id, event)
            case ScheduleSaved():
                await self._render_schedule(chat_id, business_id, event)
            case OnboardingComplete():
                if event.scope == "business":
                    await self._send_hire_card(chat_id, None, ui.SETUP_DONE)
            case Error():
                thread_id = self._thread_for(event.team_id) if event.team_id else run.thread_id
                await self._chat.send(chat_id, thread_id, ui.error_text(event))

    async def _show_status(self, run: _Run, thread_id: int | None, text: str) -> None:
        run.status_threads.add(thread_id)
        message_id = self._state.status.get((run.chat_id, thread_id))
        if message_id is None:
            message_id = await self._chat.send(run.chat_id, thread_id, text)
            self._state.status[(run.chat_id, thread_id)] = message_id
        else:
            await self._chat.edit(run.chat_id, message_id, text)

    async def _render_ask(self, chat_id: int, business_id: str, event: Ask) -> None:
        thread_id = self._thread_for(event.team_id)
        self._asking.add(self._key(business_id, event.team_id))
        key = _short_id()
        text = ui.ask_text(event)
        keyboard = ui.ask_keyboard(key, event.quick_replies)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        if event.quick_replies:
            self._state.asks[key] = AskCard(
                chat_id, thread_id, message_id, text, keyboard,
                business_id=business_id, team_id=event.team_id, replies=event.quick_replies,
            )

    async def _image_bytes(self, business_id: str, images: list[FileRef]) -> list[bytes]:
        found = []
        for image in images:
            data = await self._files.read(business_id, image.file_id)
            if data is None:
                logger.warning("Image %s is missing; showing the draft without it", image.file_id)
            else:
                found.append(data)
        return found

    async def _send_with_images(
        self,
        chat_id: int,
        thread_id: int | None,
        business_id: str,
        images: list[FileRef],
        text: str,
        keyboard: Keyboard | None = None,
        text_above: Callable[[bool], str] | None = None,
    ) -> tuple[int, str]:
        """
        One image with a short text: a photo with the text as its caption. Otherwise the
        images first (one photo, or an album: albums can't carry buttons), then the text.
        Returns the message that has the text and buttons, and its text. `text_above`
        rewrites the text for when the images are in the message above.
        """
        photos = await self._image_bytes(business_id, images)
        if len(photos) == 1 and len(text) <= ui.CAPTION_LIMIT:
            sent = await self._chat.send_photo(chat_id, thread_id, photos[0], text, keyboard)
            return sent, text
        if len(photos) == 1:
            await self._chat.send_photo(chat_id, thread_id, photos[0])
        elif photos:
            await self._chat.send_album(chat_id, thread_id, photos)
        if photos and text_above is not None:
            text = text_above(True)
        return await self._chat.send(chat_id, thread_id, text, keyboard), text

    async def _render_approval(self, chat_id: int, business_id: str, event: NeedsApproval) -> None:
        thread_id = self._thread_for(event.team_id)
        images = ui.draft_images(event)
        editable = event.planned_action is not None or not event.media
        keyboard = ui.approval_keyboard(event.approval_id, editable, ui.made_images(event))
        above = partial(ui.approval_text, event)
        message_id, text = await self._send_with_images(
            chat_id, thread_id, business_id, images, above(False), keyboard, above
        )
        self._state.approvals[event.approval_id] = ApprovalCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, team_id=event.team_id, approval_id=event.approval_id,
            persona=event.persona, action=event.planned_action, task_id=event.task_id,
        )

    async def _render_action(self, chat_id: int, business_id: str, event: ActionDone) -> None:
        thread_id = self._thread_for(event.team_id)
        text = ui.action_text(event)
        keyboard = ui.undo_keyboard(event.action_id) if event.undo_until else []
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.actions[event.action_id] = ActionCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, team_id=event.team_id, summary=event.summary,
            undo_until=event.undo_until,
        )

    async def _render_undone(self, chat_id: int, event: ActionUndone) -> None:
        card = self._state.actions.get(event.action_id)
        if card is None:
            await self._chat.send(chat_id, self._thread_for(event.team_id), ui.text(event.summary))
            return
        await self._chat.edit(card.chat_id, card.message_id, ui.undone_text(card.summary, event))

    async def _render_offer(self, chat_id: int, business_id: str, event: PromotionOffer) -> None:
        thread_id = self._thread_for(event.team_id)
        key = _short_id()
        text = ui.promotion_text(event)
        keyboard = ui.promotion_keyboard(key)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.offers[key] = OfferCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, team_id=event.team_id, task_type=event.task_type,
            persona=event.persona, proposed=event.proposed_level,
        )

    async def _render_lesson(self, chat_id: int, business_id: str, event: LessonLearned) -> None:
        thread_id = self._thread_for(event.team_id)
        team_name = self._state.team_names.get(event.team_id or "")
        text = ui.lesson_text(event, team_name)
        keyboard = ui.forget_keyboard(event.lesson_id)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.lessons[event.lesson_id] = LessonCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, team_id=event.team_id, persona=event.persona,
        )

    async def _render_schedule(
        self, chat_id: int, business_id: str, event: ScheduleSaved
    ) -> None:
        schedule = event.schedule
        if schedule.active and schedule.next_run_at is None:
            schedule = schedule.model_copy(
                update={"next_run_at": next_run(schedule.cadence, self._clock())}
            )
            await self._store.save_schedule(schedule)
        old = self._state.schedules.get(schedule.schedule_id)
        if old is not None:
            await self._close(old, None)
        thread_id = self._thread_for(event.team_id)
        text = ui.schedule_text(event.persona, schedule)
        keyboard = ui.schedule_keyboard(schedule)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.schedules[schedule.schedule_id] = ScheduleCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, team_id=event.team_id, persona=event.persona,
            schedule=schedule,
        )

    async def _refresh_schedule(self, schedule: Schedule) -> None:
        card = self._state.schedules.get(schedule.schedule_id)
        if card is None:
            return
        running = schedule.schedule_id in self._running
        card.schedule = schedule
        card.text = ui.schedule_text(card.persona, schedule, running)
        card.keyboard = ui.schedule_keyboard(schedule, running)
        self._state.schedules[schedule.schedule_id] = card
        try:
            await self._chat.edit(card.chat_id, card.message_id, card.text, card.keyboard)
        except Exception:
            logger.debug("Updating the schedule card failed", exc_info=True)

    async def _render_hired(self, chat_id: int, business_id: str, event: TeamHired) -> None:
        icon = ui.TOPIC_ICONS.get(event.template)
        thread_id = await self._chat.create_topic(chat_id, event.display_name, icon)
        self._state.add_team(chat_id, thread_id, event.team_id, event.display_name)
        topic = TelegramTopic(
            team_id=event.team_id,
            business_id=business_id,
            chat_id=chat_id,
            thread_id=thread_id,
            name=event.display_name,
        )
        await self._store.save_topic(topic)
        roster_id = await self._send_roster(chat_id, thread_id, event)
        try:
            await self._chat.pin(chat_id, roster_id)
        except Exception:
            logger.debug("Pinning the roster failed", exc_info=True)
        link = ui.open_topic_keyboard(event.display_name, ui.topic_link(chat_id, thread_id))
        await self._chat.send(chat_id, None, ui.hired_text(event), link)

    async def _send_roster(self, chat_id: int, thread_id: int, event: TeamHired) -> int:
        """
        "Meet your team": the personas' pictures with the roster as the caption, or the
        roster alone while there are no pictures.
        """
        text = ui.roster_text(event)
        pictures = [p for p in (avatar_bytes(x.avatar) for x in event.personas) if p]
        try:
            if len(pictures) == 1:
                return await self._chat.send_photo(chat_id, thread_id, pictures[0], text)
            if pictures:
                return (await self._chat.send_album(chat_id, thread_id, pictures, text))[0]
        except Exception:
            logger.warning("Sending the team's pictures failed", exc_info=True)
        return await self._chat.send(chat_id, thread_id, text)

    async def _send_hire_card(self, chat_id: int, thread_id: int | None, text: str) -> None:
        keyboard = ui.hire_keyboard(self._brain.list_templates())
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.hire_cards[(chat_id, message_id)] = Card(
            chat_id, thread_id, message_id, text, keyboard
        )

    async def _close(
        self, card: Card, footer: str | None, keyboard: Keyboard | None = None
    ) -> None:
        if keyboard is None:
            self._state.handled.add((card.chat_id, card.message_id))
        await self._chat.edit(
            card.chat_id, card.message_id, ui.with_footer(card.text, footer), keyboard
        )

    async def _close_asks(self, chat_id: int, thread_id: int | None) -> None:
        for key, card in list(self._state.asks.items()):
            if card.chat_id == chat_id and card.thread_id == thread_id:
                del self._state.asks[key]
                await self._close(card, None)

    def _decide_edit(self, card: ApprovalCard, edited_text: str, action: PlannedAction) -> None:
        resolution = ApprovalDecision(
            business_id=card.business_id,
            approval_id=card.approval_id,
            decision="edit",
            edited_text=edited_text,
            edited_action=action,
        )
        self._spawn(
            card.chat_id,
            card.team_id,
            card.thread_id,
            lambda: self._brain.resolve_approval(resolution),
        )

    def _decide(self, card: ApprovalCard, decision: str, **fields: str) -> None:
        resolution = ApprovalDecision(
            business_id=card.business_id,
            approval_id=card.approval_id,
            decision=decision,
            **fields,
        )
        self._spawn(
            card.chat_id,
            card.team_id,
            card.thread_id,
            lambda: self._brain.resolve_approval(resolution),
        )

    async def _answer_pending(
        self, chat_id: int, thread_id: int | None, pending: Pending, text: str
    ) -> None:
        card = self._state.approvals[pending.approval_id]
        if pending.kind in ("text", "subject", "to"):
            await self._answer_field(chat_id, thread_id, card, pending, text)
            return
        if pending.kind == "edit":
            if isinstance(card.action, PostSocial) and len(text) > ui.POST_LIMIT:
                await self._chat.send(chat_id, thread_id, ui.too_long(len(text)))
                return
            del self._state.pending[(chat_id, thread_id)]
            await self._close(card, ui.EDITED)
            self._decide(card, "edit", edited_text=text)
            return
        del self._state.pending[(chat_id, thread_id)]
        if pending.kind == "image":
            await self._close(card, f"{ui.NEW_IMAGE_REQUESTED}: {text}")
            self._decide(card, "new_image", reason=text)
            return
        await self._close(card, ui.REJECTED)
        self._decide(card, "reject", reason=text)

    async def revisions_left(self, task_id: str | None) -> bool:
        task = await self._store.get_task(task_id) if task_id else None
        return task is None or task.revisions < REVISIONS_INCLUDED

    async def _tap_new_image(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        if not await self.revisions_left(card.task_id):
            return ui.NO_REVISIONS_LEFT
        await self._close(card, ui.NEW_IMAGE_ASK, ui.new_image_keyboard(approval_id))
        return None

    async def _tap_image_reason(
        self, chat_id: int, message_id: int, approval_id: str
    ) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        await self._close(card, ui.NEW_IMAGE_WAITING)
        self._state.pending[(card.chat_id, card.thread_id)] = Pending("image", approval_id)
        await self._chat.send(card.chat_id, card.thread_id, ui.IMAGE_REASON_PROMPT)
        return None

    async def _tap_image_again(
        self, chat_id: int, message_id: int, approval_id: str
    ) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        await self._close(card, ui.NEW_IMAGE_REQUESTED)
        self._decide(card, "new_image")
        return None

    async def _tap_approve(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        await self._close(card, ui.APPROVED)
        self._decide(card, "approve")
        return None

    async def _tap_edit(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        if isinstance(card.action, SendEmail) or (card.action and card.action.images):
            await self._close(card, ui.EDITING_BELOW)
            draft = EditDraft(approval_id, card.action, card.action, [])
            await self._show_edit(card, draft, menu=True)
            return None
        await self._close(card, ui.EDITING)
        self._state.pending[(card.chat_id, card.thread_id)] = Pending("edit", approval_id)
        await self._chat.send(card.chat_id, card.thread_id, ui.edit_prompt(card.action))
        return None

    async def _show_edit(self, card: ApprovalCard, draft: EditDraft, menu: bool) -> None:
        """
        Shows the edit menu or the founder's version, in one message that's edited in place.
        """
        if menu:
            text = ui.edit_menu_text(draft.action)
            keyboard = ui.edit_menu_keyboard(draft.approval_id, draft.action, draft.original)
        else:
            text = ui.your_version_text(card.persona, draft.action, draft.changes)
            keyboard = ui.your_version_keyboard(draft.approval_id)
        if draft.message_id is None:
            draft.message_id = await self._chat.send(card.chat_id, card.thread_id, text, keyboard)
        else:
            await self._chat.edit(card.chat_id, draft.message_id, text, keyboard)
        self._state.edits[draft.approval_id] = draft

    async def _drop_edit(self, approval_id: str, footer: str | None) -> None:
        draft = self._state.edits.pop(approval_id, None)
        card = self._state.approvals.get(approval_id)
        if draft is None or draft.message_id is None or card is None:
            return
        text = ui.your_version_text(card.persona, draft.action, draft.changes)
        try:
            await self._chat.edit(card.chat_id, draft.message_id, ui.with_footer(text, footer))
        except Exception:
            logger.debug("Closing the edit message failed", exc_info=True)

    def _edit_parts(self, approval_id: str) -> tuple[ApprovalCard, EditDraft] | None:
        card = self._state.approvals.get(approval_id)
        draft = self._state.edits.get(approval_id)
        return (card, draft) if card is not None and draft is not None else None

    async def _tap_edit_field(self, chat_id: int, message_id: int, arg: str) -> str | None:
        approval_id, _, field = arg.partition(":")
        parts = self._edit_parts(approval_id)
        if parts is None:
            return ui.GONE
        card, draft = parts
        if field.startswith("img"):
            number = int(field[3:])
            removed = draft.original.images[number - 1]
            images = [i for i in draft.action.images if i.file_id != removed.file_id]
            draft.action = draft.action.model_copy(update={"images": images})
            draft.changes.append(f"image {number} removed")
            await self._show_edit(card, draft, menu=False)
            return None
        self._state.pending[(card.chat_id, card.thread_id)] = Pending(field, approval_id)
        await self._chat.send(card.chat_id, card.thread_id, ui.field_prompt(field, draft.action))
        return None

    async def _answer_field(
        self, chat_id: int, thread_id: int | None, card: ApprovalCard, pending: Pending, text: str
    ) -> None:
        draft = self._state.edits.get(pending.approval_id)
        if draft is None:
            del self._state.pending[(chat_id, thread_id)]
            await self._chat.send(chat_id, thread_id, ui.GONE)
            return
        value = text if pending.kind == "text" else text.strip()
        if not value.strip():
            await self._chat.send(chat_id, thread_id, ui.EMPTY_FIELD)
            return
        if pending.kind == "to" and not EMAIL_ADDRESS.fullmatch(value):
            await self._chat.send(chat_id, thread_id, ui.BAD_ADDRESS)
            return
        if isinstance(draft.action, PostSocial):
            if len(value) > ui.POST_LIMIT:
                await self._chat.send(chat_id, thread_id, ui.too_long(len(value)))
                return
            update = {"text": value}
        else:
            update = {"body" if pending.kind == "text" else pending.kind: value}
        del self._state.pending[(chat_id, thread_id)]
        draft.action = draft.action.model_copy(update=update)
        change = f"{ui.FIELD_NAMES[pending.kind]} changed"
        draft.changes = [c for c in draft.changes if c != change] + [change]
        await self._show_edit(card, draft, menu=False)

    async def _tap_edit_more(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        parts = self._edit_parts(approval_id)
        if parts is None:
            return ui.GONE
        await self._show_edit(*parts, menu=True)
        return None

    async def _tap_edit_undo(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        parts = self._edit_parts(approval_id)
        if parts is None:
            return ui.GONE
        card, _ = parts
        self._state.pending.pop((card.chat_id, card.thread_id), None)
        await self._drop_edit(approval_id, ui.EDIT_UNDONE)
        self._state.handled.discard((card.chat_id, card.message_id))
        await self._chat.edit(card.chat_id, card.message_id, card.text, card.keyboard)
        return None

    async def _tap_edit_approve(
        self, chat_id: int, message_id: int, approval_id: str
    ) -> str | None:
        parts = self._edit_parts(approval_id)
        if parts is None:
            return ui.GONE
        card, draft = parts
        if not draft.changes:
            return "Change something first, or approve the draft as it is."
        self._state.pending.pop((card.chat_id, card.thread_id), None)
        await self._drop_edit(approval_id, ui.APPROVED_YOURS)
        await self._close(card, ui.EDITED)
        action = draft.action
        edited_text = action.text if isinstance(action, PostSocial) else action.body
        self._decide_edit(card, edited_text, action)
        return None

    async def _tap_reject(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        await self._close(card, None, ui.reject_keyboard(approval_id))
        return None

    async def _tap_reason(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        await self._close(card, ui.REJECTING)
        self._state.pending[(card.chat_id, card.thread_id)] = Pending("reason", approval_id)
        await self._chat.send(card.chat_id, card.thread_id, ui.reason_prompt(card.persona))
        return None

    async def _tap_drop(self, chat_id: int, message_id: int, approval_id: str) -> str | None:
        card = self._state.approvals.get(approval_id)
        if card is None:
            return ui.GONE
        await self._close(card, ui.REJECTED)
        self._decide(card, "reject")
        return None

    async def _tap_undo(self, chat_id: int, message_id: int, action_id: str) -> str | None:
        card = self._state.actions.get(action_id)
        if card is None or card.message_id != message_id:
            return await self._business_tap(
                chat_id, message_id, self.undo_from_dashboard, action_id
            )
        await self._close(card, None)
        if card.undo_until is None or self._clock() > card.undo_until:
            return ui.UNDO_CLOSED
        self._spawn(
            card.chat_id,
            card.team_id,
            card.thread_id,
            lambda: self._brain.undo_action(card.business_id, action_id),
        )
        return None

    async def _tap_answer(self, chat_id: int, message_id: int, arg: str) -> str | None:
        key, _, index = arg.partition(":")
        card = self._state.asks.pop(key, None)
        if card is None:
            return ui.GONE
        answer = card.replies[int(index)]
        await self._close(card, f"→ {answer}")
        self._asking.discard(self._key(card.business_id, card.team_id))
        message = IncomingMessage(
            business_id=card.business_id,
            team_id=card.team_id,
            text=answer,
            message_id=str(message_id),
            sent_at=self._clock(),
        )
        self._spawn(
            card.chat_id,
            card.team_id,
            card.thread_id,
            lambda: self._brain.handle_message(message),
        )
        return None

    async def _tap_hire(self, chat_id: int, message_id: int, template: str) -> str | None:
        card = self._state.hire_cards.pop((chat_id, message_id), None)
        if card is None:
            return ui.GONE
        info = self._template(template)
        business_id = self._state.businesses[chat_id]
        outcome = await self._request_hire(chat_id, card.thread_id, business_id, template)
        if outcome == "busy":
            self._state.hire_cards[(chat_id, message_id)] = card
            return ui.ALREADY_HIRING
        footer = ui.hiring(info.display_name if info else template) if outcome == "hiring" else None
        await self._close(card, footer)
        return None

    async def _tap_replace(self, chat_id: int, message_id: int, key: str) -> str | None:
        card = self._state.replacements.pop(key, None)
        if card is None:
            return ui.GONE
        old = await self._hired(card.business_id, card.template)
        if not self._start_hire(card.chat_id, card.thread_id, card.business_id, card.template, old):
            self._state.replacements[key] = card
            return ui.ALREADY_HIRING
        await self._close(card, ui.replacing(card.name))
        return None

    async def _tap_keep(self, chat_id: int, message_id: int, key: str) -> str | None:
        card = self._state.replacements.pop(key, None)
        if card is None:
            return ui.GONE
        await self._close(card, ui.kept(card.name))
        return None

    async def _tap_promote_yes(self, chat_id: int, message_id: int, key: str) -> str | None:
        return await self._answer_offer(key, accepted=True)

    async def _tap_promote_no(self, chat_id: int, message_id: int, key: str) -> str | None:
        return await self._answer_offer(key, accepted=False)

    async def _answer_offer(self, key: str, accepted: bool) -> str | None:
        card = self._state.offers.pop(key, None)
        if card is None:
            return ui.GONE
        footer = ui.promoted(card.task_type, card.proposed) if accepted else ui.not_promoted(
            card.persona
        )
        await self._close(card, footer)
        response = PromotionResponse(
            business_id=card.business_id,
            team_id=card.team_id,
            task_type=card.task_type,
            accepted=accepted,
        )
        self._spawn(
            card.chat_id,
            card.team_id,
            card.thread_id,
            lambda: self._brain.respond_promotion(response),
        )
        return None

    async def _tap_run_now(self, chat_id: int, message_id: int, schedule_id: str) -> str | None:
        return await self._business_tap(chat_id, message_id, self.run_schedule_now, schedule_id)

    async def _tap_stop(self, chat_id: int, message_id: int, schedule_id: str) -> str | None:
        stop = partial(self.set_schedule_active, active=False)
        return await self._business_tap(chat_id, message_id, stop, schedule_id)

    async def _tap_turn_on(self, chat_id: int, message_id: int, schedule_id: str) -> str | None:
        turn_on = partial(self.set_schedule_active, active=True)
        return await self._business_tap(chat_id, message_id, turn_on, schedule_id)

    async def _business_tap(
        self, chat_id: int, message_id: int, action: Callable[..., Any], *args: Any
    ) -> str | None:
        """
        Runs a shared action for this chat's business; a refusal becomes the toast. A list
        message the tap came from is redrawn afterwards.
        """
        business_id = self._state.businesses.get(chat_id)
        if business_id is None:
            return ui.GONE
        try:
            await action(business_id, *args)
        except Refused as refused:
            return str(refused)
        finally:
            await self._rerender(chat_id, message_id)
        return None

    async def _tap_lower(self, chat_id: int, message_id: int, arg: str) -> str | None:
        team_id, _, task_type = arg.partition(":")
        team = await self._store.get_team(team_id)
        trust = await self._store.get_trust(team_id, task_type)
        if team is None or trust is None or LEVELS.index(trust.level) == 0:
            return ui.GONE
        text, keyboard = reports.lower_confirm(team, trust)
        await self._chat.edit(chat_id, message_id, text, keyboard)
        return None

    async def _tap_lower_yes(self, chat_id: int, message_id: int, arg: str) -> str | None:
        team_id, _, task_type = arg.partition(":")
        toast = await self._business_tap(chat_id, message_id, self.lower_trust, team_id, task_type)
        return toast or "Lowered. The team will ask more often from now on."

    async def _tap_lower_no(self, chat_id: int, message_id: int, team_id: str) -> str | None:
        await self._rerender(chat_id, message_id)
        return None

    async def _tap_threshold(self, chat_id: int, message_id: int, arg: str) -> str | None:
        team_id, _, number = arg.partition(":")
        trust = await self._store.list_trust(team_id)
        if trust and trust[0].promote_after == int(number):
            return None
        return await self._business_tap(
            chat_id, message_id, self.set_threshold, team_id, int(number)
        )

    async def _tap_page(self, chat_id: int, message_id: int, arg: str) -> str | None:
        view = self._views.get((chat_id, message_id))
        if view is None:
            return ui.GONE
        view.page = int(arg.partition(":")[2])
        await self._rerender(chat_id, message_id)
        return None

    async def _tap_forget(self, chat_id: int, message_id: int, lesson_id: str) -> str | None:
        card = self._state.lessons.get(lesson_id)
        if card is None:
            return await self._business_tap(chat_id, message_id, self.forget_lesson, lesson_id)
        await self.forget_lesson(card.business_id, lesson_id)
        await self._rerender(chat_id, message_id)
        return None
