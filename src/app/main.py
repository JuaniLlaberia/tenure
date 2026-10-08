"""
Runs the Telegram bot (long polling) and the dashboard (FastAPI) in one process.
Start with: uv run --env-file .env python -m app.main
"""

import asyncio
import logging
import os

import uvicorn
from telegram import Update

from app.chat.bot import build_application
from app.fake_brain import FakeBrain
from app.store.base import AppStore
from app.store.memory import InMemoryStore
from app.store.supabase_store import SupabaseStore
from app.web.api import create_api

logger = logging.getLogger(__name__)

COMMANDS = [
    ("start", "Set up your business"),
    ("hire", "Hire a team, e.g. /hire marketing"),
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

async def run(token: str) -> None:
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    dashboard_url = os.environ.get("DASHBOARD_URL", f"http://localhost:{port}")
    store = make_store()
    brain = FakeBrain(store, delay=0.8)
    application, flows = build_application(token, brain, store, dashboard_url)
    api = create_api(flows, store, brain)
    server = uvicorn.Server(uvicorn.Config(api, host=host, port=port, log_level="warning"))
    async with application:
        await flows.load()
        await application.start()
        await application.bot.set_my_commands(COMMANDS)
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Bot polling; dashboard on %s", dashboard_url)
        try:
            await server.serve()
        finally:
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
