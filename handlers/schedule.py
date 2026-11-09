"""Inline working-hours editor; uses an explicit IANA timezone."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from aiogram import F, Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from services import store
from services.schedule import allowed, validate

router = Router()
router.message.filter(F.chat.type == "private")
DAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")


class HoursInput(StatesGroup):
    zone = State()
    window = State()


def clock(minutes):
    return f"{minutes//60:02d}:{minutes%60:02d}"


def parse_window(value):
    normalized = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    match = re.fullmatch(r"\s*([0-9]{2}):([0-9]{2})\s*[-–]\s*([0-9]{2}):([0-9]{2})\s*", normalized)
    if not match:
        raise ValueError("Invalid format")
    sh, sm, eh, em = map(int, match.groups())
    if sh > 23 or sm > 59 or eh > 24 or em > 59 or (eh == 24 and em != 0):
        raise ValueError("Invalid clock")
    start, end = sh*60+sm, eh*60+em
    if start == end:
        raise ValueError("Equal times")
    return start, end


async def panel(cb):
    from handlers.menu import show, keyboard, button, back
    row = await store.get_reply_schedule(cb.from_user.id)
    local = "ابتدا منطقهٔ زمانی را انتخاب کن"
    if row.timezone:
        try:
            local = datetime.now(timezone.utc).astimezone(ZoneInfo(row.timezone)).strftime("%Y-%m-%d %H:%M %Z")
        except (ValueError, KeyError):
            local = "منطقهٔ زمانی نامعتبر؛ دوباره انتخاب کن"
    days = "، ".join(DAYS[d] for d in row.days if type(d) is int and d in range(7)) or "انتخاب نشده"
    await show(cb, "ساعت پاسخ‌گویی فیلو\n\n"
               f"برنامه: {'روشن' if row.enabled else 'خاموش؛ بدون محدودیت ساعت'}\n"
               f"منطقهٔ زمانی: {row.timezone or 'انتخاب نشده'}\nزمان محلی: {local}\n"
               f"روزهای شروع: {days}\nبازه: {clock(row.start_minute)}–{clock(row.end_minute)}\n"
               f"وضعیت برنامه همین حالا: {'اجازهٔ پاسخ' if allowed(row) else 'سکوت خارج از ساعت'}\n\n"
               "هر روز یک بازهٔ مشترک دارد. اگر پایان از شروع کوچک‌تر باشد، بازه تا روز بعد ادامه دارد. "
               "خارج از ساعت، نه AI فراخوانی می‌شود و نه پاسخ ثابت می‌فرستیم؛ تأیید پیش‌نویس هم مسدود است. "
               "تغییرات ذخیره‌شده روی برنامهٔ روشن فوراً اعمال می‌شوند. مکث و تحویل به مالک مستقل‌اند.", keyboard(
                   [button("🌍 منطقهٔ زمانی", "hours:zones"), button("📅 روزها", "hours:days")],
                   [button("🕒 ویرایش بازه", "hours:window")],
                   [button("خاموش؛ پاسخ در همهٔ ساعت‌ها", "hours:off", "danger") if row.enabled else
                    button("فعال‌سازی برنامه…", "hours:ask", "primary")], back()))


async def navigate(cb, state, action, pending):
    from handlers.menu import show, keyboard, button, back
    uid = cb.from_user.id
    try:
        if action == "hours:home":
            await panel(cb)
        elif action == "hours:zones":
            await show(cb, "منطقهٔ زمانی حساب را انتخاب کن؛ ساعت سرور استفاده نمی‌شود. تغییر ساعت تابستانی تابع قواعد همین منطقه است.", keyboard(
                [button("تهران", "hours:zone:Asia/Tehran"), button("برلین", "hours:zone:Europe/Berlin")],
                [button("UTC", "hours:zone:UTC"), button("منطقهٔ دیگر…", "hours:custom")],
                [button("بازگشت", "hours:home")]))
        elif action.startswith("hours:zone:") and action[11:] in {"Asia/Tehran", "Europe/Berlin", "UTC"}:
            await store.update_reply_schedule(uid, timezone=action[11:])
            await panel(cb)
        elif action == "hours:custom":
            await state.set_state(HoursInput.zone)
            await show(cb, "نام IANA منطقهٔ زمانی را بفرست، مثلاً Europe/London یا Asia/Dubai. برای لغو /cancel.",
                       keyboard([button("انصراف", "hours:home")]))
        elif action == "hours:days" or action.startswith("hours:day:"):
            row = await store.get_reply_schedule(uid)
            if action.startswith("hours:day:"):
                value = action[10:]
                if value not in {str(d) for d in range(7)}:
                    raise ValueError("Invalid day")
                day = int(value)
                days = set(row.days)
                days.symmetric_difference_update({day})
                row = await store.update_reply_schedule(uid, days=sorted(days))
            await show(cb, "روزهای شروع بازه را انتخاب کن. در بازهٔ شبانه، بخش پس از نیمه‌شب متعلق به روز شروع است. "
                       "برای برنامهٔ روشن حداقل یک روز باید انتخاب شود.", keyboard(
                           *[[button(("✅ " if d in row.days else "▫️ ")+DAYS[d], f"hours:day:{d}")
                              for d in pair] for pair in ((5,6),(0,1),(2,3),(4,))],
                           [button("بازگشت", "hours:home")]))
        elif action == "hours:window":
            await state.set_state(HoursInput.window)
            await show(cb, "بازه را به شکل 09:00-17:00 یا 22:00-02:00 بفرست. این بازه برای همهٔ روزهای انتخاب‌شده مشترک است. "
                       "برای کل روز 00:00-24:00 را بفرست. شروع شامل و پایان خارج از بازه است. /cancel برای لغو.",
                       keyboard([button("انصراف", "hours:home")]))
        elif action == "hours:ask":
            row = await store.get_reply_schedule(uid)
            validate(row.timezone, row.days, row.start_minute, row.end_minute)
            await state.update_data(confirmation="hours:on", schedule_revision=row.revision)
            await show(cb, f"برنامهٔ {clock(row.start_minute)}–{clock(row.end_minute)} در {row.timezone} فعال شود؟\n"
                       "روزها: " + "، ".join(DAYS[d] for d in row.days) +
                       "\nبیرون از این زمان‌ها ارسال خودکار و تأیید پیش‌نویس متوقف می‌شوند. برنامهٔ روشن، مکث کلی یا تحویل به مالک را لغو نمی‌کند.", keyboard(
                           [button("بله، فعال شود", "hours:on", "success")], [button("انصراف", "hours:home")]))
        elif action == "hours:on":
            await store.update_reply_schedule(uid, enabled=True, expected_revision=pending.get("schedule_revision", -1))
            await panel(cb)
        elif action == "hours:off":
            await store.update_reply_schedule(uid, enabled=False)
            await panel(cb)
        else:
            raise ValueError("Unknown action")
    except ValueError:
        await show(cb, "ذخیره نشد. منطقهٔ زمانی، حداقل یک روز و بازهٔ معتبر را بررسی کن. "
                   "اگر تنظیمات بعد از تأیید تغییر کرده‌اند، تأیید جدید بگیر.", keyboard([button("تنظیم ساعت‌ها", "hours:home")], back()))


@router.message(HoursInput.zone, F.text, ~F.text.startswith("/"))
async def save_zone(message: Message, state: FSMContext):
    from handlers.menu import button, keyboard
    value = message.text.strip()
    try:
        validate(value, [0], 540, 1020)
        await store.update_reply_schedule(message.from_user.id, timezone=value)
    except ValueError:
        await message.answer("نام منطقه معتبر نیست؛ یک نام IANA مانند Asia/Tehran بفرست یا /cancel.")
        return
    await state.clear()
    await message.answer("منطقهٔ زمانی ذخیره شد.", reply_markup=keyboard([button("تنظیم ساعت‌ها", "hours:home")]))


@router.message(HoursInput.window, F.text, ~F.text.startswith("/"))
async def save_window(message: Message, state: FSMContext):
    from handlers.menu import button, keyboard
    try:
        start, end = parse_window(message.text)
        await store.update_reply_schedule(message.from_user.id, start_minute=start, end_minute=end)
    except ValueError:
        await message.answer("بازه معتبر نیست. مثلاً 09:00-17:00 بفرست؛ شروع و پایان برابر نباشند. /cancel برای لغو.")
        return
    await state.clear()
    await message.answer("بازه ذخیره شد.", reply_markup=keyboard([button("تنظیم ساعت‌ها", "hours:home")]))


@router.message(HoursInput.zone, ~F.text)
@router.message(HoursInput.window, ~F.text)
async def nontext(message: Message):
    await message.answer("یک پیام متنی بفرست؛ برای لغو /cancel.")
