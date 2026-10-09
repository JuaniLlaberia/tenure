"""
Runs the Telegram bot (long polling) and the dashboard (FastAPI) in one process.
Start with: uv run --env-file .env python -m app.main
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

@asynccontextmanager
async def open_brain(store: AppStore, tools: Tools) -> AsyncIterator[Brain]:
    """
    Juan's brain when OPENROUTER_API_KEY is set, with its LangGraph checkpointer on
    DATABASE_URL (in memory without it). Otherwise the fake brain, so the app still runs.
    """
    settings = Settings.from_env(os.environ)
    if settings.openrouter_api_key is None:
        logger.warning("OPENROUTER_API_KEY not set: using the fake brain")
        yield FakeBrain(store, tools=tools, delay=0.8)
        return
    if settings.database_url is None:
        logger.warning("DATABASE_URL not set: brain conversations are lost on restart")
    async with open_checkpointer(settings) as checkpointer:
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
        await flows.load()
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
