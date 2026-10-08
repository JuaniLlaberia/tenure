"""
Turns Telegram input into Brain calls and renders the event streams back into the chat.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import uuid4

from app.chat import ui
from app.chat.port import Chat, Keyboard
from app.chat.state import (
    ActionCard,
    ApprovalCard,
    AppState,
    AskCard,
    Card,
    LessonCard,
    OfferCard,
    Pending,
    ReplaceCard,
)
from app.store.base import AppStore, TelegramTopic
from app.store.memory import InMemoryStore
from app.web import auth
from contract import (
    ActionDone,
    ActionUndone,
    ApprovalDecision,
    Ask,
    Brain,
    Error,
    Event,
    IncomingMessage,
    LessonLearned,
    NeedsApproval,
    OnboardingComplete,
    PostSocial,
    Progress,
    PromotionOffer,
    PromotionResponse,
    Say,
    Team,
    TeamHired,
    TemplateInfo,
)

logger = logging.getLogger(__name__)

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
        self._password_ttl = password_ttl
        self._password_messages: dict[int, int] = {}
        self._timers: set[asyncio.Task] = set()
        self._chat = chat
        self._state = state or AppState()
        self._store = store or InMemoryStore()
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
            ui.REPLACE: self._tap_replace,
            ui.KEEP: self._tap_keep,
        }

    async def drain(self) -> None:
        """
        Waits until every brain call started so far has finished (tests and shutdown).
        """
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def load(self) -> None:
        """
        Restores businesses and team topics from the store, so a restart keeps working.
        """
        self._state.businesses.update(await self._store.list_businesses())
        for topic in await self._store.list_topics():
            self._state.add_team(topic.chat_id, topic.thread_id, topic.team_id, topic.name)

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
        self._state.handled.discard((card.chat_id, card.message_id))
        await self._chat.edit(card.chat_id, card.message_id, card.text, card.keyboard)
        await self._chat.send(chat_id, thread_id, ui.CANCELLED)

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
        self._password_messages[chat_id] = message_id
        timer = asyncio.create_task(self._expire_password_message(chat_id, message_id))
        self._timers.add(timer)
        timer.add_done_callback(self._timers.discard)

    async def on_dashboard_stop(self, chat_id: int, thread_id: int | None) -> None:
        business_id = await self._business(chat_id, thread_id)
        if business_id is None:
            return
        await self._store.set_dashboard(business_id, None, None)
        await self._delete_password_message(chat_id)
        await self._chat.send(chat_id, thread_id, ui.DASHBOARD_STOPPED)

    async def _expire_password_message(self, chat_id: int, message_id: int) -> None:
        await asyncio.sleep(self._password_ttl)
        if self._password_messages.get(chat_id) == message_id:
            await self._delete_password_message(chat_id)

    async def _delete_password_message(self, chat_id: int) -> None:
        message_id = self._password_messages.pop(chat_id, None)
        if message_id is None:
            return
        try:
            await self._chat.delete(chat_id, message_id)
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
        self, business_id: str, approval_id: str, approve: bool, reason: str | None = None
    ) -> None:
        """
        Approve or reject a draft from the dashboard, as if tapped in Telegram.
        """
        approval = await self._store.get_approval(approval_id)
        if approval is None or approval.business_id != business_id:
            raise Refused("I can't find that draft.")
        card = self._state.approvals.get(approval_id)
        busy = card is not None and (card.chat_id, card.message_id) in self._state.handled
        if approval.status != "pending" or busy or approval_id in self._deciding:
            raise Refused("That draft is already being handled.")
        chat_id = self._chat_for(business_id)
        self._deciding.add(approval_id)
        if card is not None:
            footer = ui.APPROVED_ON_DASHBOARD if approve else ui.REJECTED_ON_DASHBOARD
            await self._close(card, footer)
        decision = ApprovalDecision(
            business_id=business_id,
            approval_id=approval_id,
            decision="approve" if approve else "reject",
            reason=reason.strip() if reason and reason.strip() else None,
        )
        self._track(self._decide_and_release(chat_id, approval.team_id, decision))

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
    ) -> None:
        key = self._key(business_id, team_id)
        buffer = self._buffers.get(key)
        if buffer is not None:
            buffer.texts.append(text)
            buffer.message_id = message_id
            buffer.sent_at = sent_at
            return
        self._buffers[key] = _Buffer([text], message_id, sent_at)
        self._track(self._flush_later(key, chat_id, thread_id, business_id, team_id))

    async def _flush_later(
        self,
        key: str,
        chat_id: int,
        thread_id: int | None,
        business_id: str,
        team_id: str | None,
    ) -> None:
        while True:
            seen = len(self._buffers[key].texts)
            await asyncio.sleep(self._debounce)
            if len(self._buffers[key].texts) == seen:
                break
        buffer = self._buffers.pop(key)
        message = IncomingMessage(
            business_id=business_id,
            team_id=team_id,
            text="\n".join(buffer.texts),
            message_id=str(buffer.message_id),
            sent_at=buffer.sent_at,
        )
        await self._call(chat_id, team_id, thread_id, lambda: self._brain.handle_message(message))

    def _key(self, business_id: str, team_id: str | None) -> str:
        return f"{business_id}:{team_id or 'company'}"

    def _track(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _spawn(
        self, chat_id: int, team_id: str | None, thread_id: int | None, events: Events
    ) -> None:
        self._track(self._call(chat_id, team_id, thread_id, events))

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
                await self._chat.send(chat_id, self._thread_for(event.team_id), ui.say_text(event))
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
        key = _short_id()
        text = ui.ask_text(event)
        keyboard = ui.ask_keyboard(key, event.quick_replies)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        if event.quick_replies:
            self._state.asks[key] = AskCard(
                chat_id, thread_id, message_id, text, keyboard,
                business_id=business_id, team_id=event.team_id, replies=event.quick_replies,
            )

    async def _render_approval(self, chat_id: int, business_id: str, event: NeedsApproval) -> None:
        thread_id = self._thread_for(event.team_id)
        text = ui.approval_text(event)
        keyboard = ui.approval_keyboard(event.approval_id)
        message_id = await self._chat.send(chat_id, thread_id, text, keyboard)
        self._state.approvals[event.approval_id] = ApprovalCard(
            chat_id, thread_id, message_id, text, keyboard,
            business_id=business_id, team_id=event.team_id, approval_id=event.approval_id,
            persona=event.persona, action=event.planned_action,
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

    async def _render_hired(self, chat_id: int, business_id: str, event: TeamHired) -> None:
        thread_id = await self._chat.create_topic(chat_id, event.display_name)
        self._state.add_team(chat_id, thread_id, event.team_id, event.display_name)
        topic = TelegramTopic(
            team_id=event.team_id,
            business_id=business_id,
            chat_id=chat_id,
            thread_id=thread_id,
            name=event.display_name,
        )
        await self._store.save_topic(topic)
        roster_id = await self._chat.send(chat_id, thread_id, ui.roster_text(event))
        try:
            await self._chat.pin(chat_id, roster_id)
        except Exception:
            logger.debug("Pinning the roster failed", exc_info=True)
        link = ui.open_topic_keyboard(event.display_name, ui.topic_link(chat_id, thread_id))
        await self._chat.send(chat_id, None, ui.hired_text(event), link)

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
        if pending.kind == "edit":
            if isinstance(card.action, PostSocial) and len(text) > ui.POST_LIMIT:
                await self._chat.send(chat_id, thread_id, ui.too_long(len(text)))
                return
            del self._state.pending[(chat_id, thread_id)]
            await self._close(card, ui.EDITED)
            self._decide(card, "edit", edited_text=text)
            return
        del self._state.pending[(chat_id, thread_id)]
        await self._close(card, ui.REJECTED)
        self._decide(card, "reject", reason=text)

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
        await self._close(card, ui.EDITING)
        self._state.pending[(card.chat_id, card.thread_id)] = Pending("edit", approval_id)
        await self._chat.send(card.chat_id, card.thread_id, ui.edit_prompt(card.action))
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
        if card is None:
            return ui.GONE
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

    async def _tap_forget(self, chat_id: int, message_id: int, lesson_id: str) -> str | None:
        card = self._state.lessons.get(lesson_id)
        if card is None:
            return ui.GONE
        await self.forget_lesson(card.business_id, lesson_id)
        return None
