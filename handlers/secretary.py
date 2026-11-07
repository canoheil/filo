"""Business/secretary mode: business_connection + business_message handlers.
Safe defaults: draft-for-approval, ignore own outgoing echoes & other bots,
idempotency on (connection, chat, message)."""

from aiogram import Router, F
from aiogram import Bot
from aiogram.types import BusinessConnection, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import get_settings
from core.logging import setup_logging
from services import store, persona as persona_svc, ai
from services.quota import quota_exhausted, utc_today

logger = setup_logging(get_settings().LOG_LEVEL)
router = Router()


@router.business_connection()
async def on_business_connection(event: BusinessConnection, bot: Bot) -> None:
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
            await bot.send_message(event.user.id, "🔗 سکرتر وصل شد! از حالا پیام‌های دایرکتت را می‌توانم جواب بدهم.\nبا /whoami تنظیماتت را ببین.")
        else:
            await bot.send_message(event.user.id, "🔌 اتصال از طرف تلگرام قطع شد. اگر اشتباه است دوباره از Chat Automation وصل کن.")
    except Exception as e:  # owner may have blocked the bot in DM
        logger.warning("notify owner failed: %s", e)


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
    if sender_id == conn.user_id:  # owner's own outgoing message echoed back
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        return  # stickers/photos without caption: skip for now (Pro feature later)

    # --- Idempotency: Telegram retries --------------------------------------
    is_new = await store.log_inbound_once(connection_id, message.chat.id, message.message_id)
    if not is_new:
        return

    p = await store.get_or_create_persona(conn.user_id)
    if p.is_paused:
        return

    user = await store.get_or_create_user(conn.user_id, None)

    # --- Quota (free tier: SHARED AI Studio key, so this is the safety rail) -
    allowed, used = await store.quota_hit_and_count(user.tg_user_id, settings.FREE_DAILY_LIMIT)
    if not allowed:
        try:
            await bot.send_message(conn.user_id, f"⚠️ سقف رایگان امروزت تمام شد ({settings.FREE_DAILY_LIMIT} جواب/روز).\nبرای ادامه، پلن Pro را فعال کن یا فردا برگرد.")
        except Exception:
            pass
        logger.info("quota overflow user=%s", user.tg_user_id)
        return

    system_prompt = persona_svc.build_system_prompt(p)
    try:
        reply = await ai.generate_reply(system_prompt, text)
    except Exception as e:
        logger.error("AI failure conn=%s: %s", connection_id, e)
        try:
            await bot.send_message(conn.user_id, "❗ موقع جواب دادن به یک پیام به مشکل خوردم. لاگ را چک کن.")
        except Exception:
            pass
        return

    if p.auto_reply:
        sent = await bot.send_message(chat_id=message.chat.id, text=reply,
                                      business_connection_id=connection_id)
        await store.log_outbound(connection_id, message.chat.id, sent.message_id, "ok")
        return

    # --- Draft mode (default): owner approves in DM --------------------------
    draft_id = await store.save_draft(connection_id, message.chat.id, message.message_id, reply)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ارسال", callback_data=f"draft:approve:{draft_id}"),
        InlineKeyboardButton(text="✖ رد", callback_data=f"draft:reject:{draft_id}"),
    ]])
    preview = f"✍️ پیش‌نویس برای جواب به «{(message.from_user.first_name if message.from_user else 'کاربر')}»:\n\n{reply}"
    await bot.send_message(conn.user_id, preview[:4000], reply_markup=kb)


@router.callback_query(F.data.startswith("draft:"))
async def on_draft_action(cb: CallbackQuery, bot: Bot) -> None:
    _, action, draft_id_raw = cb.data.split(":", 2)
    draft_id = int(draft_id_raw)
    row = await store.take_draft(draft_id)
    if row is None or row.status != "draft":
        await cb.answer("این پیش‌نویس دیگر معتبر نیست.", show_alert=True)
        return
    if action == "reject":
        await store.finish_draft(draft_id, "rejected")
        await cb.message.edit_text((cb.message.text or "") + "\n\n✖ رد شد.")
        await cb.answer()
        return
    sent = await bot.send_message(chat_id=row.chat_id, text=row.draft_text or "",
                                  business_connection_id=row.connection_id)
    await store.finish_draft(draft_id, "sent")
    await store.log_outbound(row.connection_id, row.chat_id, sent.message_id, "ok")
    await cb.message.edit_text((cb.message.text or "") + "\n\n✅ ارسال شد.")
    await cb.answer()
