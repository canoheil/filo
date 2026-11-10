"""Entry point. Long polling on purpose (shared cPanel + watchdog pattern from sib).
Handlers are transport-agnostic, so a future webhook front (e.g. Cloudflare) is a wrapper away."""

import asyncio
import sys
from contextlib import suppress
from services.context import recent_context
from services.outbound import outbound

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from core.config import get_settings
from core.logging import setup_logging
from database.connection import init_db
from handlers import register_all_handlers
from middleware.rate_limit import RateLimitMiddleware
from middleware.menu_state import CommandClearsMenuState
from middleware.membership import MembershipMiddleware
from middleware.broadcast_audience import BroadcastAudienceMiddleware

ALLOWED_UPDATES = [
    "message", "callback_query",
    "business_connection", "business_message",
    "edited_business_message", "deleted_business_messages",
]


async def cleanup_context(limiter=None) -> None:
    while True:
        await asyncio.sleep(30)
        recent_context.purge()
        outbound.purge()
        from services.ai_guard import ai_guard
        ai_guard.purge(ai_guard.clock())
        if limiter is not None:
            limiter.purge()


async def cleanup_drafts() -> None:
    from services.draft_lifecycle import maintenance
    while True:
        try:
            await maintenance()
        except Exception as exc:
            import logging
            logging.getLogger("secretary").warning("draft maintenance failed type=%s", type(exc).__name__)
        await asyncio.sleep(60)


async def main() -> None:
    settings = get_settings()
    logger = setup_logging(settings.LOG_LEVEL)
    if not settings.BOT_TOKEN:
        sys.exit("BOT_TOKEN is missing — copy .env.example to .env and fill it in.")

    await init_db()  # v1 convenience; use alembic for schema changes after launch
    from services import admin
    from services.membership import membership
    await admin.initialize()
    membership.channel_loader = admin.snapshot

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
    await outbound.restore()
    bot.session.middleware(outbound)
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    dp.message.middleware(CommandClearsMenuState())
    input_limiter = RateLimitMiddleware(settings.INPUT_RATE_SECONDS,
                                       max_entries=settings.INPUT_RATE_MAX_USERS)
    dp.message.outer_middleware(input_limiter)
    dp.callback_query.outer_middleware(input_limiter)
    dp.message.outer_middleware(BroadcastAudienceMiddleware())
    dp.message.outer_middleware(MembershipMiddleware())
    dp.callback_query.outer_middleware(MembershipMiddleware())
    register_all_handlers(dp)

    await bot.set_my_commands([
        BotCommand(command="menu", description="منوی اصلی و تنظیمات دستیار"),
        BotCommand(command="drafts", description="پیش‌نویس‌ها و وضعیت ارسال"),
        BotCommand(command="cancel", description="لغو ویرایش و بازگشت به منو"),
        BotCommand(command="help", description="راهنما و دستورات پیشرفته"),
    ], scope=BotCommandScopeAllPrivateChats())
    logger.info("secretary-bot starting (polling)…")
    from services import broadcast
    await broadcast.recover()
    cleanup = asyncio.create_task(cleanup_context(input_limiter))
    drafts_cleanup = asyncio.create_task(cleanup_drafts())
    broadcaster = asyncio.create_task(broadcast.worker(bot))
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        broadcaster.cancel()
        cleanup.cancel()
        drafts_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await broadcaster
        with suppress(asyncio.CancelledError):
            await cleanup
        with suppress(asyncio.CancelledError):
            await drafts_cleanup


if __name__ == "__main__":
    asyncio.run(main())
