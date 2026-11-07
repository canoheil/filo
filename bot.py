"""Entry point. Long polling on purpose (shared cPanel + watchdog pattern from sib).
Handlers are transport-agnostic, so a future webhook front (e.g. Cloudflare) is a wrapper away."""

import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from core.config import get_settings
from core.logging import setup_logging
from database.connection import init_db
from handlers import register_all_handlers
from middleware.rate_limit import RateLimitMiddleware

ALLOWED_UPDATES = [
    "message", "callback_query",
    "business_connection", "business_message",
    "edited_business_message", "deleted_business_messages",
]


async def main() -> None:
    settings = get_settings()
    logger = setup_logging(settings.LOG_LEVEL)
    if not settings.BOT_TOKEN:
        sys.exit("BOT_TOKEN is missing — copy .env.example to .env and fill it in.")

    await init_db()  # v1 convenience; use alembic for schema changes after launch

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.message.outer_middleware(RateLimitMiddleware())
    dp.callback_query.outer_middleware(RateLimitMiddleware())
    register_all_handlers(dp)

    logger.info("secretary-bot starting (polling)…")
    await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    asyncio.run(main())
