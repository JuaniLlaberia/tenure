"""
python-telegram-bot wiring: the real Chat, and handlers that feed Flows.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeVar

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.chat import ui
from app.chat.flows import Flows
from app.chat.port import Keyboard
from app.store.base import AppStore
from contract import Brain

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
ATTEMPTS = 3
TIMEOUT = 15

T = TypeVar("T")

async def _retry(call: Callable[[], Awaitable[T]], repeatable: bool) -> T:
    """
    Waits out Telegram's flood control and, for calls that are safe to repeat,
    retries timeouts. A timed-out send is not retried: it may have been delivered.
    """
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return await call()
        except RetryAfter as error:
            if attempt == ATTEMPTS:
                raise
            wait = error.retry_after
            seconds = wait.total_seconds() if isinstance(wait, timedelta) else float(wait)
            logger.warning("Telegram flood control, waiting %.0f s", seconds)
            await asyncio.sleep(seconds + 0.5)
        except TimedOut:
            if not repeatable or attempt == ATTEMPTS:
                raise
            logger.warning("Telegram timed out, retrying")
    raise AssertionError("unreachable")

def _markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    if not keyboard:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b.text, callback_data=b.data, url=b.url) for b in row]
            for row in keyboard
        ]
    )

def _thread(message: Message) -> int | None:
    return message.message_thread_id if message.is_topic_message else None

class TelegramChat:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, keyboard: Keyboard | None = None
    ) -> int:
        message = await _retry(
            lambda: self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                message_thread_id=thread_id,
                reply_markup=_markup(keyboard),
                link_preview_options=NO_PREVIEW,
            ),
            repeatable=False,
        )
        return message.message_id

    async def edit(
        self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None
    ) -> None:
        try:
            await _retry(
                lambda: self._bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_markup(keyboard),
                    link_preview_options=NO_PREVIEW,
                ),
                repeatable=True,
            )
        except BadRequest as error:
            if "not modified" not in str(error).lower():
                raise

    async def delete(self, chat_id: int, message_id: int) -> None:
        await _retry(
            lambda: self._bot.delete_message(chat_id=chat_id, message_id=message_id),
            repeatable=True,
        )

    async def create_topic(self, chat_id: int, name: str) -> int:
        topic = await _retry(
            lambda: self._bot.create_forum_topic(chat_id=chat_id, name=name), repeatable=False
        )
        return topic.message_thread_id

    async def pin(self, chat_id: int, message_id: int) -> None:
        await _retry(
            lambda: self._bot.pin_chat_message(
                chat_id=chat_id, message_id=message_id, disable_notification=True
            ),
            repeatable=True,
        )

    async def typing(self, chat_id: int, thread_id: int | None) -> None:
        await self._bot.send_chat_action(
            chat_id=chat_id, action=ChatAction.TYPING, message_thread_id=thread_id
        )

def build_application(
    token: str, brain: Brain, store: AppStore, dashboard_url: str
) -> tuple[Application, Flows]:
    application = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .connect_timeout(TIMEOUT)
        .read_timeout(TIMEOUT)
        .write_timeout(TIMEOUT)
        .pool_timeout(TIMEOUT)
        .build()
    )
    flows = Flows(brain, TelegramChat(application.bot), store=store, dashboard_url=dashboard_url)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat.type == ChatType.PRIVATE:
            await message.reply_text(ui.PRIVATE_HINT)
            return
        await flows.on_start(chat.id, _thread(message), bool(chat.is_forum))

    async def hire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        name = " ".join(context.args or []) or None
        await flows.on_hire(message.chat_id, _thread(message), name)

    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_cancel(message.chat_id, _thread(message))

    async def help_(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_help(message.chat_id, _thread(message))

    async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_dashboard(message.chat_id, _thread(message))

    async def text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message.from_user is None or message.from_user.is_bot:
            return
        await flows.on_text(
            message.chat_id, _thread(message), message.text, message.message_id, message.date
        )

    async def private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(ui.PRIVATE_HINT)

    async def tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        toast = None
        try:
            if query.message is not None and query.data:
                toast = await flows.on_callback(
                    query.message.chat.id, query.message.message_id, query.data
                )
        finally:
            try:
                await query.answer(toast)
            except Exception:
                logger.debug("Answering the tap failed", exc_info=True)

    async def failed(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram update failed", exc_info=context.error)

    groups = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("hire", hire, filters=groups))
    application.add_handler(CommandHandler("cancel", cancel, filters=groups))
    application.add_handler(CommandHandler("help", help_, filters=groups))
    application.add_handler(CommandHandler("dashboard", dashboard, filters=groups))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & groups, text))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, private))
    application.add_handler(CallbackQueryHandler(tap))
    application.add_error_handler(failed)
    return application, flows
