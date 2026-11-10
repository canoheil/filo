"""Business/secretary mode: business_connection + business_message handlers.
Safe defaults: draft-for-approval, ignore own outgoing echoes & other bots,
idempotency on (connection, chat, message)."""

import asyncio
from services.ai_guard import ai_guard
from services.protection import metrics
from services.outbound import OutboundDeferred

from aiogram import Router, F
from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import BusinessConnection, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import get_settings
from core.draft_ui import draft_keyboard, valid_number
from core.logging import setup_logging
from services import store, persona as persona_svc, ai, chat_control, usage
from services.context import recent_context
from services.membership import membership
from services.draft_lifecycle import expired, expire_claim, caption
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

logger = setup_logging(get_settings().LOG_LEVEL)
router = Router()


@router.business_connection()
async def on_business_connection(event: BusinessConnection, bot: Bot) -> None:
    await store.get_or_create_user(event.user.id, event.user.username)
    if not event.is_enabled:
        recent_context.clear_owner(event.user.id)
    rights = event.rights.model_dump() if event.rights else None
    await store.upsert_connection(
        connection_id=event.id,
        user_id=event.user.id,
        rights=rights,
        tg_chat_id=event.user_chat_id,
        is_enabled=event.is_enabled,
    )
    try:
        if event.is_enabled:
            await bot.send_message(event.user.id,
                "🔗 اتصال فیلو ثبت شد.\n" +
                ("مجوز پاسخ فعال است؛ ارسال تابع حالت پیش‌نویس/خودکار و مکث توست."
                 if event.rights and event.rights.can_reply else "مجوز پاسخ فعال نیست؛ دسترسی اتصال را بررسی کن.") +
                "\nبا /menu تنظیمات و بررسی اتصال را ببین.")
        else:
            await bot.send_message(event.user.id, "🔌 اتصال از طرف تلگرام قطع شد. اگر اشتباه است دوباره از Chat Automation وصل کن.")
    except Exception as e:  # owner may have blocked the bot in DM
        logger.warning("notify owner failed type=%s", type(e).__name__)


@router.business_message()
async def on_business_message(message: Message, bot: Bot) -> None:
    settings = get_settings()
    connection_id = message.business_connection_id
    if not connection_id:
        return

    # --- Guards against loops / self-replies -------------------------------
    conn = await store.get_connection(connection_id)
    if conn is None or not conn.is_enabled:
        return
    # message sent BY a business bot (possibly us on another connection) -> ignore
    if getattr(message, "sender_business_bot", None):
        return
    sender_id = message.from_user.id if message.from_user else None
    if sender_id == conn.user_id:  # real owner replies may supply context, never trigger AI
        p = await store.get_or_create_persona(conn.user_id)
        await chat_control.owner_replied(conn.user_id, connection_id, message.chat.id, message.message_id,
                                         getattr(p, "handoff_minutes", 0))
        if getattr(p, "context_enabled", False):
            recent_context.sent(conn.user_id, connection_id, message.chat.id,
                                message.text or message.caption or "")
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        return  # stickers/photos without caption: skip for now (Pro feature later)

    if len(text) > settings.AI_BUSINESS_MAX_CHARS:
        metrics.record("ai.oversized")
        return
    async with ai_guard.slot(conn.user_id, (connection_id, message.chat.id)) as admission:
        if not admission.allowed:
            return
        # --- Idempotency: Telegram retries --------------------------------------
        is_new = await store.log_inbound_once(connection_id, message.chat.id, message.message_id)
        if not is_new:
            return

        access = await membership.check(bot, conn.user_id)
        if access.state != "member":
            reason = "membership" if access.state == "missing" else "membership_error"
            await chat_control.ensure(conn.user_id, connection_id, message.chat.id)
            await chat_control.note(conn.user_id, connection_id, message.chat.id, reason)
            await usage.inbound(connection_id, message.chat.id, message.message_id, reason)
            return

        routing = await chat_control.check(conn.user_id, connection_id, message.chat.id)
        if routing != "active":
            await chat_control.note(conn.user_id, connection_id, message.chat.id, routing)
            await usage.inbound(connection_id, message.chat.id, message.message_id, routing)
            return
        p = await store.get_or_create_persona(conn.user_id)
        if p.is_paused:
            await chat_control.note(conn.user_id, connection_id, message.chat.id, "global_paused")
            await usage.inbound(connection_id, message.chat.id, message.message_id, "global_paused")
            return

        routing_version = await chat_control.version(conn.user_id, connection_id, message.chat.id)
        user = await store.get_or_create_user(conn.user_id, None)

        # --- Quota (free tier: SHARED AI Studio key, so this is the safety rail) -
        request_key = usage.business_key(conn.user_id, connection_id, message.chat.id, message.message_id)
        allowed, used = await store.quota_hit_and_count(user.tg_user_id, settings.FREE_DAILY_LIMIT,
                                                      request_key=request_key, source="business")
        if not allowed:
            await chat_control.note(conn.user_id, connection_id, message.chat.id, "quota")
            await usage.inbound(connection_id, message.chat.id, message.message_id, "quota")
            try:
                if await ai_guard.notice(conn.user_id, "quota"):
                    await bot.send_message(conn.user_id, f"⚠️ سقف رایگان امروزت تمام شد ({settings.FREE_DAILY_LIMIT} جواب/روز).\nبرای ادامه، پلن Pro را فعال کن یا فردا برگرد.")
            except Exception:
                pass
            logger.info("quota overflow user=%s", user.tg_user_id)
            return

        await chat_control.note(conn.user_id, connection_id, message.chat.id, "active")
        await usage.inbound(connection_id, message.chat.id, message.message_id, "ai_reserved")
        if getattr(p, "context_enabled", False):
            # Recheck consent after quota I/O; do not start caching using a stale flag.
            p = await store.get_or_create_persona(conn.user_id)
        system_prompt = persona_svc.build_system_prompt(p, use_case=getattr(user, "use_case", "business"))
        history = None
        if getattr(p, "context_enabled", False):
            key, ticket, history = recent_context.begin(conn.user_id, connection_id, message.chat.id)
            recent_context.append(key, ticket, "user", text)
        else:
            recent_context.clear_owner(conn.user_id)
        try:
            async with asyncio.timeout(settings.AI_REQUEST_TIMEOUT_SECONDS):
                reply = (await ai.generate_reply(system_prompt, text, history=history)
                         if history is not None else await ai.generate_reply(system_prompt, text))
        except Exception as e:
            await usage.finish(conn.user_id, request_key, e)
            await usage.inbound(connection_id, message.chat.id, message.message_id, "ai_failed")
            logger.error("AI failure type=%s", type(e).__name__)
            try:
                if await ai_guard.notice(conn.user_id, "ai_error"):
                    await bot.send_message(conn.user_id, "❗ موقع جواب دادن به یک پیام به مشکل خوردم. لاگ را چک کن.")
            except Exception:
                pass
            return

        await usage.finish(conn.user_id, request_key)
        await usage.inbound(connection_id, message.chat.id, message.message_id, "ai_ok")
        # A takeover/pause/resume during AI invalidates that in-flight response, even
        # if the temporary pause has already been lifted before generation finishes.
        if (await chat_control.version(conn.user_id, connection_id, message.chat.id) != routing_version
                or await chat_control.check(conn.user_id, connection_id, message.chat.id) != "active"):
            await chat_control.note(conn.user_id, connection_id, message.chat.id, "reply_superseded")
            await usage.inbound(connection_id, message.chat.id, message.message_id, "reply_superseded")
            return
        # Persist the generated reply before any delivery, including auto mode.
        draft_id = await store.save_draft(connection_id, message.chat.id, message.message_id, reply)
        # Settings may have changed while the AI request was in flight.
        current = await store.get_or_create_persona(conn.user_id)
        if current.is_paused:
            return  # keep a pending draft; do not send while paused
        if current.auto_reply:
            row = await store.claim_draft(draft_id, conn.user_id)
            if row is not None:
                notice = await _deliver_draft(row, draft_id, conn.user_id, bot)
                if notice != "✅ ارسال شد.":
                    try:
                        await bot.send_message(conn.user_id, f"پاسخ خودکار #{draft_id}\n{notice}", parse_mode=None)
                    except Exception as exc:
                        logger.warning("auto notification failed type=%s", type(exc).__name__)
            return

        # --- Draft mode (default): owner approves in DM --------------------------
        kb = draft_keyboard(draft_id)
        preview = f"✍️ پیش‌نویس #{draft_id} برای جواب به «{(message.from_user.first_name if message.from_user else 'کاربر')}»:"
        try:
            await bot.send_message(conn.user_id, preview, parse_mode=None)
            await bot.send_message(conn.user_id, reply, reply_markup=kb, parse_mode=None)
        except Exception as exc:
            logger.warning("draft preview failed id=%s type=%s", draft_id, type(exc).__name__)


@router.callback_query(F.data.startswith("draft:"))
async def on_draft_action(cb: CallbackQuery, bot: Bot) -> None:
    parts = (cb.data or "").split(":")
    if (len(parts) not in {3, 4} or parts[1] not in {"approve", "reject"}
            or not valid_number(parts[2]) or (len(parts) == 4 and not valid_number(parts[3], zero=True))):
        await cb.answer("درخواست نامعتبر است.", show_alert=True)
        return
    action, draft_id = parts[1], int(parts[2])
    revision = int(parts[3]) if len(parts) == 4 else 0
    row = await store.claim_draft(draft_id, cb.from_user.id, reject=action == "reject", expected_revision=revision)
    if row is None:
        await cb.answer("این نسخه در دسترس نیست، تغییر کرده یا مهلت ارسالش تمام شده.", show_alert=True)
        return
    # Acknowledge before network work; callback queries have a short lifetime.
    try:
        await cb.answer()
    except Exception as exc:
        logger.warning("callback acknowledgement failed type=%s", type(exc).__name__)
    if action == "reject":
        await _show_draft_result(cb, "✖ رد شد.")
        return

    notice = await _deliver_draft(row, draft_id, cb.from_user.id, bot)
    await _show_draft_result(cb, notice)


async def _deliver_draft(row, draft_id: int, owner_id: int, bot: Bot) -> str:
    """One claimed delivery only. Never retry an ambiguous Telegram send."""
    try:
        # Fetch current rights rather than trusting the stored connection snapshot.
        conn = await bot.get_business_connection(row.connection_id)
        if (conn.user.id != owner_id or not conn.is_enabled
                or not conn.rights or not conn.rights.can_reply):
            await store.set_draft_delivery_state(draft_id, "blocked")
            return "⚠️ ارسال نشد؛ اتصال یا مجوز پاسخ فعال نیست."
    except Exception as exc:
        logger.warning("draft connection check failed id=%s type=%s", draft_id, type(exc).__name__)
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "⚠️ امکان بررسی اتصال نبود؛ ارسال انجام نشد."

    try:
        current = await store.get_or_create_persona(owner_id)
        if current.is_paused:
            await store.set_draft_delivery_state(draft_id, "blocked")
            return "⏸ ارسال نشد؛ دستیار در حالت مکث است."
    except Exception as exc:
        logger.warning("delivery settings check failed type=%s", type(exc).__name__)
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "⚠️ بررسی تنظیمات ممکن نشد؛ ارسال انجام نشد."

    access = await membership.check(bot, owner_id)
    if access.state != "member":
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "ارسال نشد؛ عضویت مالک در کانال‌ها تأیید نشده. از /start بررسی دوباره را انجام بده."

    try:
        routing = await chat_control.check(owner_id, row.connection_id, row.chat_id)
        if routing != "active":
            await chat_control.note(owner_id, row.connection_id, row.chat_id, routing)
            await store.set_draft_delivery_state(draft_id, "blocked")
            return ("🕒 ارسال نشد؛ اکنون خارج از ساعت پاسخ‌گویی است." if routing == "outside_hours" else
                    "⏸ ارسال نشد؛ گفتگو متوقف یا موقتاً در اختیار صاحب حساب است.")
    except Exception as exc:
        logger.warning("chat routing check failed type=%s", type(exc).__name__)
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "⚠️ بررسی کنترل گفتگو ناموفق بود؛ ارسال انجام نشد."
    if expired(row):
        await expire_claim(draft_id)
        return "⌛ مهلت ارسال تمام شد؛ متن پیش‌نویس پاک شد."
    try:
        sent = await bot.send_message(
            chat_id=row.chat_id, text=row.draft_text or "",
            business_connection_id=row.connection_id, parse_mode=None)
    except (OutboundDeferred, TelegramRetryAfter):
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "⚠️ ارسال انجام نشد؛ محدودیت موقت ارسال فعال است. ارسال خودکار تکرار نمی‌شود."
    except (TelegramBadRequest, TelegramForbiddenError):
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "⚠️ تلگرام ارسال را نپذیرفت؛ پیش‌نویس نگه داشته شد."
    except Exception as exc:
        logger.warning("draft delivery uncertain id=%s type=%s", draft_id, type(exc).__name__)
        await store.set_draft_delivery_state(draft_id, "uncertain")
        return "⚠️ وضعیت ارسال نامشخص است؛ قبل از هر ارسال دیگری چت مقصد را بررسی کن."

    # Only a confirmed Telegram send may become an assistant context message.
    if getattr(current, "context_enabled", False):
        recent_context.sent(owner_id, row.connection_id, row.chat_id, row.draft_text or "")
    # Persist success before UI work: an edit failure must never allow a resend.
    await store.finish_draft(draft_id, "sent")
    await store.log_outbound(row.connection_id, row.chat_id, sent.message_id, "ok")
    return "✅ ارسال شد."


async def _show_draft_result(cb: CallbackQuery, notice: str) -> None:
    if not isinstance(cb.message, Message):
        return
    try:
        # Replace the preview too: approved/rejected text should not remain in the bot DM.
        await cb.message.edit_text(notice, reply_markup=None, parse_mode=None)
    except Exception as exc:
        logger.warning("draft UI update failed type=%s", type(exc).__name__)


@router.message(Command("drafts"), F.chat.type == "private")
async def cmd_drafts(message: Message) -> None:
    if message.from_user is None:
        return
    rows = await store.list_open_drafts(message.from_user.id)
    if not rows:
        await message.answer("پیش‌نویس بازی نداری.", parse_mode=None)
        return
    labels = {"draft": "در انتظار تأیید", "sending": "ارسال شروع شده؛ نیازمند بررسی",
              "blocked": "ارسال انجام نشد", "uncertain": "وضعیت ارسال نامشخص", "expired": "منقضی؛ غیرقابل ارسال"}
    from handlers.menu import keyboard, button
    await message.answer(
        "آخرین پیش‌نویس‌های باز (حداکثر ۲۰ مورد):\n" +
        "\n".join(f"#{row.id} — {labels[row.status]}{caption(row)}" for row in rows) +
        "\nموارد نامشخص را قبل از هر ارسال دیگری در چت مقصد بررسی کن.",
        reply_markup=keyboard([button("مدیریت و بستن موارد", "drafts")]), parse_mode=None)
    for row in rows:
        if row.status == "draft":
            kb = draft_keyboard(row.id, getattr(row, "revision", 0))
            await message.answer(f"پیش‌نویس #{row.id} — نسخهٔ {getattr(row, 'revision', 0)}", parse_mode=None)
            await message.answer(row.draft_text or "(متن خالی)", reply_markup=kb, parse_mode=None)


@router.edited_business_message()
async def clear_edited_context(message: Message):
    if message.business_connection_id:
        recent_context.clear_chat(message.business_connection_id, message.chat.id)


@router.deleted_business_messages()
async def clear_deleted_context(event):
    recent_context.clear_chat(event.business_connection_id, event.chat.id)
