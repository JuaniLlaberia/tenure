"""
Runs the Telegram bot with long polling. Start with:
uv run --env-file .env python -m app.main
"""

import logging
import os

from telegram import Update

from app.chat.bot import build_application
from app.fake_brain import FakeBrain

def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Add it to .env (see .env.example).")
    application = build_application(token, FakeBrain(delay=0.8))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
