"""
Runs the Telegram bot (long polling) and the dashboard (FastAPI) in one process.
Start with: uv run --env-file .env python -m app.main
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TypeVar

import uvicorn
from telegram import Update

from app.chat.bot import build_application
from app.fake_brain import FakeBrain
from app.files import Files
from app.scheduler import Scheduler
from app.store.base import AppStore
from app.store.memory import InMemoryStore
from app.store.supabase_store import SupabaseStore
from app.tools.real import RealTools
from app.web.api import create_api
from brain.brain import create_brain
from brain.checkpoint import open_checkpointer
from brain.deps import Settings
from contract import Brain, Tools

logger = logging.getLogger(__name__)

T = TypeVar("T")
STARTUP_TRIES = 5
FIRST_WAIT = 2.0

COMMANDS = [
    ("start", "Set up your business"),
    ("hire", "Hire a team, e.g. /hire marketing"),
    ("drafts", "Everything waiting for your OK"),
    ("team", "Trust per task; lower it or change when the team asks for more"),
    ("schedules", "Repeating work: run, stop or turn back on"),
    ("knowledge", "What the team knows about you, with Forget"),
    ("activity", "What went out, with Undo, and this week's tasks"),
    ("spend", "What the models cost this week"),
    ("dashboard", "Get the dashboard link and a new password"),
    ("dashboard_stop", "Turn the dashboard off"),
    ("cancel", "Stop an edit or a reason you started"),
    ("help", "What the bot can do"),
]

def make_store() -> AppStore:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        logger.info("Using the Supabase store")
        return SupabaseStore(url, key)
    logger.warning("SUPABASE_URL or SUPABASE_SERVICE_KEY not set: data stays in memory")
    return InMemoryStore()

async def with_retries(
    what: str,
    step: Callable[[], Awaitable[T]],
    tries: int = STARTUP_TRIES,
    wait: float = FIRST_WAIT,
) -> T:
    """
    Runs one startup step that needs the database, waiting longer after each failure, so a
    slow or briefly unreachable Supabase doesn't stop the app. Gives up with a clear message.
    """
    for attempt in range(1, tries + 1):
        try:
            return await step()
        except Exception as error:
            if attempt == tries:
                raise SystemExit(
                    f"Couldn't {what} after {tries} tries ({type(error).__name__}: {error}). "
                    "Check that Supabase is up and the settings in .env are right."
                ) from error
            logger.warning(
                "Couldn't %s (%s); trying again in %.0f s", what, type(error).__name__, wait
            )
            await asyncio.sleep(wait)
            wait *= 2
    raise AssertionError("unreachable")

@asynccontextmanager
async def open_brain(store: AppStore, tools: Tools) -> AsyncIterator[Brain]:
    """
    Juan's brain when OPENROUTER_API_KEY is set, with its LangGraph checkpointer on
    DATABASE_URL (in memory without it). The fake brain only when FAKE_BRAIN=1, so a missing
    or mistyped key stops the app instead of quietly answering with scripted drafts.
    """
    settings = Settings.from_env(os.environ)
    if os.environ.get("FAKE_BRAIN", "").strip() == "1":
        logger.warning("FAKE_BRAIN=1: using the fake brain, no models are called")
        yield FakeBrain(store, tools=tools, delay=0.8)
        return
    if settings.openrouter_api_key is None:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set, so the brain can't run. Add it to .env, or set "
            "FAKE_BRAIN=1 to try the app with scripted drafts."
        )
    if settings.database_url is None:
        logger.warning("DATABASE_URL not set: brain conversations are lost on restart")
    async with AsyncExitStack() as stack:
        checkpointer = await with_retries(
            "open the brain's database",
            lambda: stack.enter_async_context(open_checkpointer(settings)),
        )
        logger.info("Using the real brain")
        yield create_brain(store, tools, settings, checkpointer)

async def run(token: str) -> None:
    store = make_store()
    tools = RealTools.from_env(files=Files(store))
    async with open_brain(store, tools) as brain:
        await serve(token, brain, store, tools)

async def serve(token: str, brain: Brain, store: AppStore, tools: RealTools) -> None:
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    dashboard_url = os.environ.get("DASHBOARD_URL", f"http://localhost:{port}")
    application, flows = build_application(token, brain, store, dashboard_url)
    api = create_api(flows, store, brain)
    server = uvicorn.Server(uvicorn.Config(api, host=host, port=port, log_level="warning"))
    async with application:
        await with_retries("load saved state from the store", flows.load)
        await tools.check()
        await application.start()
        await application.bot.set_my_commands(COMMANDS)
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Bot polling; dashboard on %s", dashboard_url)
        schedules = asyncio.create_task(Scheduler(store, flows).run_forever())
        try:
            await server.serve()
        finally:
            schedules.cancel()
            await application.updater.stop()
            await application.stop()
            await flows.drain()

def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Add it to .env (see .env.example).")
    asyncio.run(run(token))

if __name__ == "__main__":
    main()
