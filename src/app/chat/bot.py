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
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ChatAction, ChatMemberStatus, ChatType, ParseMode
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
from app.chat.port import IncomingFile, Keyboard, TopicGone
from app.store.base import AppStore
from contract import Brain

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
ATTEMPTS = 3
TIMEOUT = 15
REPORTS = ("drafts", "team", "schedules", "knowledge", "activity", "spend")

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
            [
                InlineKeyboardButton(
                    b.text,
                    callback_data=b.data,
                    url=b.url,
                    copy_text=CopyTextButton(b.copy) if b.copy else None,
                )
                for b in row
            ]
            for row in keyboard
        ]
    )

def _thread(message: Message) -> int | None:
    return message.message_thread_id if message.is_topic_message else None

def _reply_to(message: Message) -> int | None:
    """
    The message this one answers, unless it's the topic's opening message (in a topic every
    message counts as a reply to it).
    """
    replied = message.reply_to_message
    if replied is None or replied.forum_topic_created is not None:
        return None
    return replied.message_id

async def _to_thread(thread_id: int | None, call: Callable[[], Awaitable[T]]) -> T:
    """
    Turns Telegram's "thread not found" into TopicGone, so the flows can open the topic again.
    """
    try:
        return await call()
    except BadRequest as error:
        if thread_id is not None and "thread not found" in str(error).lower():
            raise TopicGone(thread_id) from error
        raise

def _incoming_file(message: Message) -> IncomingFile | None:
    """
    The photo (largest size), voice note, audio, document or video in a message.
    """
    if message.photo:
        photo = message.photo[-1]
        return IncomingFile(photo.file_id, "image/jpeg", None, photo.file_size)
    for media, default in (
        (message.voice, "audio/ogg"),
        (message.audio, "audio/mpeg"),
        (message.document, "application/octet-stream"),
        (message.video, "video/mp4"),
        (message.video_note, "video/mp4"),
    ):
        if media is not None:
            return IncomingFile(
                media.file_id,
                getattr(media, "mime_type", None) or default,
                getattr(media, "file_name", None),
                media.file_size,
            )
    return None

class TelegramChat:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._icons: dict[str, str] | None = None

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, keyboard: Keyboard | None = None
    ) -> int:
        message = await _to_thread(
            thread_id,
            lambda: _retry(
                lambda: self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=thread_id,
                    reply_markup=_markup(keyboard),
                    link_preview_options=NO_PREVIEW,
                ),
                repeatable=False,
            ),
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
            reason = str(error).lower()
            if "no text in the message" in reason:
                await self._edit_caption(chat_id, message_id, text, keyboard)
            elif "not modified" not in reason:
                raise

    async def _edit_caption(
        self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None
    ) -> None:
        try:
            await _retry(
                lambda: self._bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_markup(keyboard),
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

    async def create_topic(self, chat_id: int, name: str, icon: str | None = None) -> int:
        icon_id = await self._icon_id(icon) if icon else None
        topic = await _retry(
            lambda: self._bot.create_forum_topic(
                chat_id=chat_id, name=name, icon_custom_emoji_id=icon_id
            ),
            repeatable=False,
        )
        return topic.message_thread_id

    async def _icon_id(self, emoji: str) -> str | None:
        """
        The custom emoji id of a topic icon, if Telegram's preset icons include this emoji.
        """
        if self._icons is None:
            try:
                stickers = await self._bot.get_forum_topic_icon_stickers()
            except Exception:
                logger.debug("Couldn't load topic icons", exc_info=True)
                return None
            self._icons = {
                (s.emoji or "").replace("\ufe0f", ""): s.custom_emoji_id for s in stickers
            }
        return self._icons.get(emoji.replace("\ufe0f", ""))

    async def delete_topic(self, chat_id: int, thread_id: int) -> None:
        await _retry(
            lambda: self._bot.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id),
            repeatable=True,
        )

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

    async def send_photo(
        self,
        chat_id: int,
        thread_id: int | None,
        photo: bytes,
        caption: str = "",
        keyboard: Keyboard | None = None,
    ) -> int:
        message = await _to_thread(
            thread_id,
            lambda: _retry(
                lambda: self._bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=thread_id,
                    reply_markup=_markup(keyboard),
                ),
                repeatable=False,
            ),
        )
        return message.message_id

    async def send_album(
        self, chat_id: int, thread_id: int | None, photos: list[bytes], caption: str = ""
    ) -> list[int]:
        media = [
            InputMediaPhoto(photo, caption=caption or None, parse_mode=ParseMode.HTML)
            if n == 0
            else InputMediaPhoto(photo)
            for n, photo in enumerate(photos)
        ]
        messages = await _to_thread(
            thread_id,
            lambda: _retry(
                lambda: self._bot.send_media_group(
                    chat_id=chat_id, media=media, message_thread_id=thread_id
                ),
                repeatable=False,
            ),
        )
        return [message.message_id for message in messages]

    async def download(self, telegram_id: str) -> bytes:
        file = await _retry(lambda: self._bot.get_file(telegram_id), repeatable=True)
        return bytes(await file.download_as_bytearray())

    async def can_manage_topics(self, chat_id: int) -> bool:
        member = await _retry(
            lambda: self._bot.get_chat_member(chat_id, self._bot.id), repeatable=True
        )
        if member.status == ChatMemberStatus.OWNER:
            return True
        return member.status == ChatMemberStatus.ADMINISTRATOR and bool(
            getattr(member, "can_manage_topics", False)
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

    async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_stop(message.chat_id, _thread(message))

    async def help_(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_help(message.chat_id, _thread(message))

    def report(kind: str):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            message = update.effective_message
            await flows.on_report(message.chat_id, _thread(message), kind)

        return handler

    async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_dashboard(message.chat_id, _thread(message))

    async def dashboard_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        await flows.on_dashboard_stop(message.chat_id, _thread(message))

    async def text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message.from_user is None or message.from_user.is_bot:
            return
        await flows.on_text(
            message.chat_id,
            _thread(message),
            message.text,
            message.message_id,
            message.date,
            _reply_to(message),
        )

    async def media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        file = _incoming_file(message)
        if message.from_user is None or message.from_user.is_bot or file is None:
            return
        await flows.on_file(
            message.chat_id,
            _thread(message),
            message.caption or "",
            file,
            message.message_id,
            message.date,
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

    new = filters.UpdateType.MESSAGE
    groups = filters.ChatType.GROUPS & new
    application.add_handler(CommandHandler("start", start, filters=new))
    application.add_handler(CommandHandler("hire", hire, filters=groups))
    application.add_handler(CommandHandler("cancel", cancel, filters=groups))
    application.add_handler(CommandHandler("stop", stop, filters=groups))
    application.add_handler(CommandHandler("help", help_, filters=groups))
    application.add_handler(CommandHandler("dashboard", dashboard, filters=groups))
    for kind in REPORTS:
        application.add_handler(CommandHandler(kind, report(kind), filters=groups))
    application.add_handler(CommandHandler("dashboard_stop", dashboard_stop, filters=groups))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & groups, text))
    files = (
        filters.PHOTO
        | filters.VOICE
        | filters.AUDIO
        | filters.Document.ALL
        | filters.VIDEO
        | filters.VIDEO_NOTE
    )
    application.add_handler(MessageHandler(files & groups, media))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & new, private))
    application.add_handler(CallbackQueryHandler(tap))
    application.add_error_handler(failed)
    return application, flows
