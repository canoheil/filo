"""Business/secretary mode: business_connection + business_message handlers.
Safe defaults: draft-for-approval, ignore own outgoing echoes & other bots,
idempotency on (connection, chat, message)."""

from aiogram import Router, F
from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import BusinessConnection, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import get_settings
from core.logging import setup_logging
from services import store, persona as persona_svc, ai
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = setup_logging(get_settings().LOG_LEVEL)
router = Router()


@router.business_connection()
async def on_business_connection(event: BusinessConnection, bot: Bot) -> None:
    await store.get_or_create_user(event.user.id, event.user.username)
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
            await bot.send_message(event.user.id, "🔗 فیلو وصل شد! از حالا پیام‌های دایرکتت را می‌توانم جواب بدهم.\nبا /menu تنظیمات دستیار را ببین.")
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
        logger.error("AI failure type=%s", type(e).__name__)
        try:
            await bot.send_message(conn.user_id, "❗ موقع جواب دادن به یک پیام به مشکل خوردم. لاگ را چک کن.")
        except Exception:
            pass
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ارسال", callback_data=f"draft:approve:{draft_id}", style="success"),
        InlineKeyboardButton(text="✖ رد", callback_data=f"draft:reject:{draft_id}", style="danger"),
    ]])
    preview = f"✍️ پیش‌نویس برای جواب به «{(message.from_user.first_name if message.from_user else 'کاربر')}»:\n\n{reply}"
    try:
        await bot.send_message(conn.user_id, preview[:2000], reply_markup=kb, parse_mode=None)
    except Exception as exc:
        logger.warning("draft preview failed id=%s type=%s", draft_id, type(exc).__name__)


@router.callback_query(F.data.startswith("draft:"))
async def on_draft_action(cb: CallbackQuery, bot: Bot) -> None:
    parts = (cb.data or "").split(":")
    if (len(parts) != 3 or parts[1] not in {"approve", "reject"}
            or not parts[2].isascii() or not parts[2].isdigit()
            or len(parts[2]) > 19 or not 0 < int(parts[2]) <= 2**63 - 1):
        await cb.answer("درخواست نامعتبر است.", show_alert=True)
        return
    action, draft_id = parts[1], int(parts[2])
    row = await store.claim_draft(draft_id, cb.from_user.id, reject=action == "reject")
    if row is None:
        await cb.answer("این پیش‌نویس در دسترس تو نیست یا قبلاً بررسی شده.", show_alert=True)
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

    try:
        sent = await bot.send_message(
            chat_id=row.chat_id, text=row.draft_text or "",
            business_connection_id=row.connection_id, parse_mode=None)
    except (TelegramBadRequest, TelegramForbiddenError):
        await store.set_draft_delivery_state(draft_id, "blocked")
        return "⚠️ تلگرام ارسال را نپذیرفت؛ پیش‌نویس نگه داشته شد."
    except Exception as exc:
        logger.warning("draft delivery uncertain id=%s type=%s", draft_id, type(exc).__name__)
        await store.set_draft_delivery_state(draft_id, "uncertain")
        return "⚠️ وضعیت ارسال نامشخص است؛ قبل از هر ارسال دیگری چت مقصد را بررسی کن."

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
              "blocked": "ارسال انجام نشد", "uncertain": "وضعیت ارسال نامشخص"}
    await message.answer(
        "آخرین پیش‌نویس‌های باز (حداکثر ۲۰ مورد):\n" +
        "\n".join(f"#{row.id} — {labels[row.status]}" for row in rows) +
        "\nموارد نامشخص را قبل از هر ارسال دیگری در چت مقصد بررسی کن.", parse_mode=None)
    for row in rows:
        if row.status == "draft":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ ارسال", callback_data=f"draft:approve:{row.id}", style="success"),
                InlineKeyboardButton(text="✖ رد", callback_data=f"draft:reject:{row.id}", style="danger"),
            ]])
            await message.answer(f"پیش‌نویس #{row.id}\n{(row.draft_text or '')[:1800]}",
                                 reply_markup=kb, parse_mode=None)
