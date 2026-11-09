"""Private usage and actionable diagnostics. Reports do not send AI requests."""
from datetime import datetime
from core.config import get_settings
from core.draft_ui import valid_number
from services import store, usage, schedule, chat_control
from services.quota import utc_today

REASONS = {"membership": "شرط عضویت کانال‌ها", "membership_error": "خطای بررسی عضویت کانال‌ها", "chat_paused": "مکث گفتگو", "owner_takeover": "تحویل به مالک", "global_paused": "مکث کلی",
           "quota": "سهمیه", "outside_hours": "خارج از ساعت", "unavailable": "کنترل در دسترس نیست",
           "reply_superseded": "کنارگذاشتن پاسخ پس از تغییر کنترل"}
DRAFTS = {"draft": "منتظر تأیید", "sent": "ارسال‌شده", "rejected": "ردشده", "expired": "منقضی",
          "blocked": "مسدود", "uncertain": "ارسال نامشخص", "sending": "ارسال آغازشده", "closed": "بسته‌شده"}
ERRORS = {"transport": "ارتباط با سرویس", "invalid_response": "پاسخ نامعتبر مدل", "http_400": "درخواست ناسازگار",
          "http_401": "کلید API نامعتبر", "http_403": "دسترسی سرویس", "http_404": "مدل یا endpoint پیدا نشد",
          "http_429": "محدودیت سرویس AI", "http_500": "اختلال سرویس", "http_502": "اختلال سرویس",
          "http_503": "اختلال سرویس", "http_504": "مهلت سرویس", "internal": "خطای داخلی/نامشخص"}


def quota_label(user):
    used = user.quota_used if user.quota_date == utc_today() else 0
    if user.plan != "free":
        return "پلن فعلی سقف روزانهٔ رایگان ندارد؛ محدودیت سرویس AI مستقل است."
    limit = get_settings().FREE_DAILY_LIMIT
    return f"مصرف سهمیه: {used} از {limit} | باقی‌مانده: {max(0, limit-used)}"


async def show_usage(cb):
    from handlers.menu import show, keyboard, button, back
    uid = cb.from_user.id
    data = await usage.report(uid)
    user = await store.get_or_create_user(uid, None)
    total = sum(count for _, state, count in data["requests"] if state != "denied")
    state_count = lambda state: sum(count for _, s, count in data["requests"] if s == state)
    business = sum(count for src, s, count in data["requests"] if src == "business" and s != "denied")
    sandbox = sum(count for src, s, count in data["requests"] if src == "sandbox" and s != "denied")
    text = (f"📊 گزارش امروز — {data['date']}، روز UTC\n\n{quota_label(user)}\n\n"
            f"رزرو درخواست AI ثبت‌شده: {total}\nگفتگو: {business} | آزمایش: {sandbox}\n"
            f"نتیجهٔ AI موفق: {state_count('succeeded')} | ناموفق: {state_count('failed')}\n"
            f"رزرو بدون نتیجهٔ ثبت‌شده: {state_count('reserved')}\n"
            f"درخواست ردشده بدون مصرف سهمیه: {state_count('denied')}\n"
            f"پاسخ واقعاً ارسال‌شده و ثبت‌شدهٔ امروز: {data['sent']}\n\n"
            "وضعیت فعلی پیش‌نویس‌های ساخته‌شده امروز:\n" +
            ("\n".join(f"{label}: {data['drafts'].get(status, 0)}" for status, label in DRAFTS.items())) +
            "\n\nموارد باز از همهٔ روزها: " + str(sum(data["open"].values())) +
            "\nمنتظر تأیید فعلی: " + str(data["open"].get("draft", 0)))
    stops = [f"{label}: {data['inbound'].get(code, 0)}" for code, label in REASONS.items() if data["inbound"].get(code)]
    if stops:
        text += "\n\nآخرین نتیجهٔ پیام‌های ورودی امروز:\n" + "\n".join(stops)
    if data["errors"]:
        text += "\n\nخطاهای AI امروز:\n" + "\n".join(
            f"{ERRORS.get(code, 'خطای نامشخص')}: {count}" for code, count in data["errors"].items())
    text += ("\n\nآمار AI از زمان نصب این نسخه ثبت می‌شود؛ سابقهٔ قبلی بازسازی نمی‌شود. رزرو، یک درخواست منطقی است؛ "
             "retry داخلی جدا شمرده نمی‌شود. موفقیت AI به معنی ارسال نیست؛ رزرو بی‌نتیجه ممکن است قطع پردازش یا خطای ثبت باشد. "
             "مصرف سهمیه شامل آزمایش و خطای AI هم هست. وضعیت‌های پیش‌نویس شمارش رویداد تاریخی نیستند.")
    await show(cb, text, keyboard([button("تازه‌سازی", "insights:usage"), button("عیب‌یابی پاسخ", "insights:health")],
                                  [button("مدیریت پیش‌نویس‌ها", "drafts")], back()))


async def show_health(cb):
    from handlers.menu import show, keyboard, button, back
    uid = cb.from_user.id
    p = await store.get_or_create_persona(uid)
    user = await store.get_or_create_user(uid, None)
    hours = await schedule.read(uid)
    connections = await store.list_user_connections(uid)
    active, reply, unknown = 0, 0, 0
    for connection in connections:
        try:
            live = await cb.bot.get_business_connection(connection.connection_id)
            if live.user.id != uid:
                unknown += 1
                continue
            active += bool(live.is_enabled)
            reply += bool(live.is_enabled and live.rights and live.rights.can_reply)
            await store.upsert_connection(live.id, uid, live.rights.model_dump() if live.rights else None,
                                          live.user_chat_id, live.is_enabled)
        except Exception:
            unknown += 1
    permitted = schedule.allowed(hours)
    used = user.quota_used if user.quota_date == utc_today() else 0
    exhausted = user.plan == "free" and used >= get_settings().FREE_DAILY_LIMIT
    text = (f"🩺 بررسی پاسخ‌دهی — {datetime.utcnow():%Y-%m-%d %H:%M} UTC\n\n"
            f"مکث کلی: {'⏸ روشن' if p.is_paused else 'خاموش'}\n"
            f"حالت: {'خودکار' if p.auto_reply else 'پیش‌نویس؛ ارسال نیازمند تأیید توست'}\n"
            f"ساعت پاسخ: {'مجاز / برنامه خاموش' if permitted else '⏸ خارج از ساعت یا برنامه نامعتبر'}\n"
            f"منطقهٔ زمانی: {hours.timezone if hours and hours.timezone else 'انتخاب نشده؛ برنامه خاموش را بررسی کن'}\n"
            f"{quota_label(user)}\n"
            f"تنظیم کلید AI: {'وجود دارد؛ اعتبار بررسی نشده' if get_settings().GEMINI_API_KEY else 'خالی است؛ نیازمند تنظیم توسط مدیر'}\n"
            f"اتصال‌های فعال تأییدشده از تلگرام: {active}\nدارای مجوز پاسخ: {reply}\n"
            f"اتصال‌های بررسی‌نشده به علت خطا/مالکیت نامعتبر: {unknown}\n\n")
    if not connections:
        text += "اتصالی در دیتابیس ثبت نشده؛ یوزرنیم بات و راهنمای اتصال را بررسی کن. این به معنی اثبات قطع در تلگرام نیست.\n"
    if unknown:
        text += "بررسی ناموفق، اثبات قطع اتصال نیست؛ دوباره بررسی کن.\n"
    if exhausted:
        text += "سهمیهٔ رایگان امروز تمام شده است.\n"
    text += ("\nاین بررسی پیام آزمایشی یا درخواست AI نمی‌فرستد و کلید AI را تأیید نمی‌کند. "
             "مجوز عمومی پاسخ، تضمین امکان ارسال در هر چت نیست؛ محدودیت‌های تلگرام و انقضای پیش‌نویس مستقل‌اند. "
             "مکث گفتگو و تحویل به مالک را برای گفتگوی موردنظر جدا بررسی کن.")
    await show(cb, text, keyboard(
        [button("حالت و مکث کلی", "mode"), button("ساعت پاسخ", "hours:home")],
        [button("راهنمای اتصال", "connection"), button("بررسی یک گفتگو", "insights:chats:0")],
        [button("پیش‌نویس‌ها", "drafts"), button("گزارش مصرف", "insights:usage")],
        [button("آزمایش AI با مصرف سهمیه", "sandbox")],
        [button("بررسی دوباره", "insights:health")], back()))


async def navigate(cb, action):
    from handlers.menu import show, keyboard, button, back
    uid = cb.from_user.id
    if action == "insights:usage":
        await show_usage(cb)
    elif action == "insights:health":
        await show_health(cb)
    elif action.startswith("insights:chats:") and valid_number(action[15:], zero=True) and int(action[15:]) < 100000:
        page = int(action[15:])
        rows = await chat_control.list_chats(uid, page)
        await show(cb, "گفتگوی موردنظر را انتخاب کن؛ نام و متن مخاطب نمایش داده نمی‌شود.", keyboard(
            *[[button(f"گفتگو {row.chat_id}", f"insights:chat:{row.id}")] for row in rows[:5]],
            *([[button("قبلی", f"insights:chats:{page-1}")]] if page else []),
            *([[button("بعدی", f"insights:chats:{page+1}")]] if len(rows) > 5 else []), back()))
    elif action.startswith("insights:chat:") and valid_number(action[14:]):
        row = await chat_control.get_chat(uid, int(action[14:]))
        if row is None:
            await show(cb, "این گفتگو در دسترس تو نیست.", keyboard(back()))
            return
        from handlers.chat_control import LABELS
        reason = chat_control.reason(row)
        persona = await store.get_or_create_persona(uid)
        user = await store.get_or_create_user(uid, None)
        within_hours = await schedule.check(uid)
        connection_label = "نامشخص؛ بررسی زنده موفق نشد"
        try:
            live = await cb.bot.get_business_connection(row.connection_id)
            if live.user.id == uid:
                connection_label = ("فعال و دارای مجوز عمومی پاسخ" if live.is_enabled and live.rights and live.rights.can_reply
                                    else "فعال نیست یا مجوز پاسخ ندارد")
        except Exception:
            pass
        await show(cb, f"گفتگو {row.chat_id}\nوضعیت محلی فعلی: {LABELS[reason]}\n"
                   f"آخرین دلیل تاریخی: {LABELS.get(row.last_reason, 'نامشخص')}\n"
                   f"مکث کلی: {'روشن' if persona.is_paused else 'خاموش'}\n"
                   f"ساعت پاسخ: {'مجاز / برنامه خاموش' if within_hours else 'خارج از ساعت'}\n"
                   f"{quota_label(user)}\nاتصال: {connection_label}\n\n"
                   "علت تاریخی، اثبات وضعیت فعلی نیست. مجوز عمومی، محدودیت‌های ارسال همان چت یا مهلت پیش‌نویس را تضمین نمی‌کند.", keyboard(
                       [button("کنترل همین گفتگو", f"chats:view:{row.id}")],
                       [button("مکث کلی", "mode"), button("ساعت پاسخ", "hours:home")],
                       [button("بررسی کلی", "insights:health"), button("پیش‌نویس‌ها", "drafts")], back()))
    else:
        await show(cb, "گزینه معتبر نیست.", keyboard(back()))
