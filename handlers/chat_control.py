"""Private control panel; callback IDs never contain message text or names."""
from datetime import datetime
from core.draft_ui import valid_number
from services import chat_control as controls, store

LABELS = {"outside_hours": "خارج از ساعت پاسخ‌گویی", "active": "فعال؛ مانع محلی ندارد", "chat_paused": "مکث همین گفتگو",
          "owner_takeover": "تحویل موقت به مالک", "global_paused": "مکث کلی",
          "quota": "سهمیه تمام شده", "reply_superseded": "پاسخ آماده‌شده پس از تغییر کنترل کنار گذاشته شد", "unavailable": "اتصال در دسترس نیست"}


async def navigate(cb, state, action):
    from handlers.menu import button, keyboard, show, back
    uid = cb.from_user.id
    if action == "chats:settings":
        p = await store.get_or_create_persona(uid)
        minutes = p.handoff_minutes
        await show(cb, f"تحویل خودکار به مالک: {str(minutes)+' دقیقه' if minutes else 'خاموش'}\n\n"
                   "وقتی خودت در گفتگوی متصل پیام بفرستی، فیلو برای مدت انتخاب‌شده کنار می‌رود. "
                   "پیام‌های ارسال‌شده توسط business bot، ورود مالک محسوب نمی‌شوند. "
                   "پیش‌نویس‌های منتظر همان گفتگو مسدود می‌شوند؛ دوباره خودکار ارسال نمی‌شوند. "
                   "فعال‌کردن، فقط از پیام بعدی مالک اثر دارد. خاموش‌کردن، تحویل‌های فعلی را لغو نمی‌کند؛ برای آن ادامهٔ همان گفتگو را بزن.", keyboard(
                       [button("خاموش", "chats:duration:0"), button("۱۵ دقیقه", "chats:duration:15")],
                       [button("۳۰ دقیقه", "chats:duration:30"), button("۶۰ دقیقه", "chats:duration:60")],
                       [button("گفتگوها", "chats:list:0")], back()))
    elif action.startswith("chats:duration:") and action[15:] in {"0", "15", "30", "60"}:
        await store.update_persona(uid, handoff_minutes=int(action[15:]))
        await show(cb, "تنظیم تحویل خودکار ذخیره شد؛ مکث کلی و حالت پاسخ تغییر نکرد.", keyboard(
            [button("تنظیمات تحویل", "chats:settings"), button("گفتگوها", "chats:list:0")], back()))
    elif action.startswith("chats:list:") and valid_number(action[11:], zero=True) and int(action[11:]) < 100000:
        page = int(action[11:])
        rows = await controls.list_chats(uid, page)
        await show(cb, "کنترل گفتگوها\n\nفقط گفتگوهایی که از زمان این نسخه آپدیت دریافت کرده‌اند نمایش داده می‌شوند. "
                   "شناسهٔ چت نمایش داده می‌شود؛ نام و متن مخاطب ذخیره نمی‌شود. "
                   "فعال در این فهرست به معنی لغو مکث کلی یا محدودیت سهمیه نیست.", keyboard(
                       *[[button(f"گفتگو {row.chat_id} — {LABELS[controls.reason(row)]}", f"chats:view:{row.id}")] for row in rows[:5]],
                       *([[button("‹ قبلی", f"chats:list:{page-1}")]] if page else []),
                       *([[button("بعدی ›", f"chats:list:{page+1}")]] if len(rows) > 5 else []),
                       [button("تنظیم تحویل به مالک", "chats:settings")], back()))
    elif action.startswith(("chats:view:", "chats:pause:", "chats:resume:")):
        parts = action.split(":")
        if len(parts) != 3 or not valid_number(parts[2]):
            return
        row = await controls.get_chat(uid, int(parts[2]))
        if row is None:
            await show(cb, "این گفتگو در دسترس تو نیست.", keyboard(back()))
            return
        if parts[1] in {"pause", "resume"}:
            await controls.set_chat(uid, row.id, parts[1] == "pause")
            row = await controls.get_chat(uid, row.id)
        persona = await store.get_or_create_persona(uid)
        from services.schedule import check
        within_hours = await check(uid)
        until = f"\nپایان تحویل موقت: {row.handoff_until:%Y-%m-%d %H:%M} UTC" if row.handoff_until and row.handoff_until > datetime.utcnow() else ""
        await show(cb, f"گفتگو: {row.chat_id}\nوضعیت فعلی: {LABELS[controls.reason(row)]}{until}\n"
                   f"مکث کلی: {'روشن' if persona.is_paused else 'خاموش'}\n"
                   f"ساعت پاسخ: {'مجاز / برنامه خاموش' if within_hours else 'خارج از ساعت؛ سکوت'}\n"
                   f"آخرین دلیل ثبت‌شده (تاریخی): {LABELS.get(row.last_reason, row.last_reason)}\n"
                   "ادامه، فقط مکث همین گفتگو و تحویل موقت آن را لغو می‌کند؛ مکث کلی و سهمیه مستقل‌اند. "
                   "پاسخ‌های قبلاً مسدودشده خودکار دوباره ارسال نمی‌شوند.", keyboard(
                       [button("⏸ مکث همین گفتگو", f"chats:pause:{row.id}"),
                        button("▶️ ادامهٔ همین گفتگو", f"chats:resume:{row.id}", "success")],
                       [button("تازه‌سازی", f"chats:view:{row.id}"), button("فهرست گفتگوها", "chats:list:0")], back()))
    else:
        await show(cb, "گزینه نامعتبر است.", keyboard(back()))
