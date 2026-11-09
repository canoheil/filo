"""Private admin panel; authorization is rechecked for every update."""
import logging
from aiogram import Router, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from core.membership import required_channels
from services import admin, broadcast

router = Router()


class AdminFlow(StatesGroup):
    channel = State()
    broadcast = State()


class AdminGuard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = event if isinstance(event, Message) else event.message
        if (not event.from_user or not admin.authorized(event.from_user.id)
                or not isinstance(message, Message) or message.chat.type != "private"
                or message.chat.id != event.from_user.id):
            await event.answer("دسترسی مجاز نیست.")
            return
        try:
            return await handler(event, data)
        except Exception as exc:
            logging.getLogger("secretary").warning("admin operation failed type=%s", type(exc).__name__)
            await message.answer("عملیات انجام نشد یا نتیجه نامشخص است؛ فهرست را تازه کن. ارسال مجدد خودکار انجام نمی‌شود.")


router.message.middleware(AdminGuard())
router.callback_query.middleware(AdminGuard())


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


async def home(message):
    users, connections = await admin.stats()
    await message.answer(f"پنل مدیریت فیلو\nکاربران ثبت‌شده: {users}\nاتصال‌های فعال ثبت‌شده: {connections}",
                         reply_markup=keyboard([[('مدیریت کانال‌ها', 'adm:list')], [('پیام سراسری', 'adm:bc')]]))


@router.message(Command("admin"))
async def entry(message, state):
    await state.clear()
    await home(message)


@router.callback_query(F.data.startswith("adm:"))
async def callback(query, state, bot):
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    if action == "bc":
        await state.clear()
        jobs = await broadcast.recent(query.from_user.id)
        await query.message.answer("ارسال سراسری به آغازکنندگان گفت‌وگوی خصوصی با بات؛ نه مخاطبان بیزینسی.\n"
            "فقط کاربرانی که پس از نصب این قابلیت در خصوصی پیام داده‌اند ثبت می‌شوند.\n۱۰ ارسال اخیر:",
            reply_markup=keyboard([[('ساخت پیام جدید', 'adm:bcnew')]] +
                [[(f'#{j.id} · {j.state}', f'adm:bcreport:{j.id}')] for j in jobs] + [[('داشبورد', 'adm:home')]]))
        return
    if action == "bcnew":
        await state.set_state(AdminFlow.broadcast)
        await query.message.answer("پیام را بفرست تا پیش‌نمایش بسازم. متن یا یک رسانهٔ مستقل؛ آلبوم پشتیبانی نمی‌شود.\n"
            "هنوز چیزی برای کاربران ارسال نمی‌شود.", reply_markup=keyboard([[('لغو', 'adm:bc')]]))
        return
    if action == "bcpreview":
        if len(parts) != 3 or not parts[2].isdigit():
            return
        result = await broadcast.report(query.from_user.id, int(parts[2]))
        if result is None:
            return await query.message.answer("پیش‌نمایش پیدا نشد.")
        job, _ = result
        await bot.copy_message(chat_id=query.from_user.id, from_chat_id=job.source_chat_id,
                               message_id=job.source_message_id, request_timeout=20)
        await show_broadcast(query.message, query.from_user.id, job.id)
        return
    if action in {"bcreport", "bcconfirm", "bcstop"}:
        if len(parts) != 3 or not parts[2].isdigit():
            return
        job_id = int(parts[2])
        if action != "bcreport":
            changed = await broadcast.control(query.from_user.id, job_id, "confirm" if action == "bcconfirm" else "stop")
            await query.message.answer("درخواست ثبت شد." if changed else "این عملیات دیگر معتبر نیست یا اجازهٔ تأیید نداری.")
        await show_broadcast(query.message, query.from_user.id, job_id)
        return
    if action == "home":
        await state.clear()
        return await home(query.message)
    if action == "add":
        await state.set_state(AdminFlow.channel)
        return await query.message.answer(
            "شناسهٔ عمومی را بفرست: @username\nبرای موارد خصوصی: شناسهٔ -100 و سپس لینک دعوت HTTPS با یک فاصله.\n"
            "بات باید ادمین باشد. حداکثر ۱۰ مورد قابل ثبت است.",
            reply_markup=keyboard([[('لغو', 'adm:list')]]))
    if action in {"enable", "disable", "delete", "confirm"}:
        if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
            return
        row_id, version = int(parts[2]), int(parts[3])
        if action == "delete":
            return await query.message.answer("این مورد از شرط عضویت حذف شود؟",
                reply_markup=keyboard([[('تأیید حذف', f'adm:confirm:{row_id}:{version}'), ('لغو', 'adm:list')]]))
        if action == "enable":
            row = next((r for r in await admin.listing() if r.id == row_id and r.version == version), None)
            if row is None or not await verify(bot, required_channels(row.chat_id, row.join_url)[0]):
                return await query.message.answer("مورد تغییر کرده یا ادمین‌بودن بات در کانال‌ها تأیید نشد؛ فهرست را تازه کن.")
        changed = await admin.mutate(query.from_user.id, 'delete' if action == 'confirm' else action,
                                     row_id=row_id, version=version)
        await query.message.answer("تغییر ذخیره شد." if changed else "این دکمه قدیمی است؛ فهرست را تازه کن.")
    elif action != "list":
        return
    await state.clear()
    rows = await admin.listing()
    await query.message.answer(f"مدیریت کانال‌ها — {len(rows)} مورد\nهمهٔ موارد فعال برای استفاده الزامی‌اند.",
        reply_markup=keyboard([[('افزودن', 'adm:add'), ('داشبورد', 'adm:home')]]))
    for row in rows:
        await query.message.answer(f"{row.chat_id}\n{'فعال' if row.enabled else 'غیرفعال'}",
            reply_markup=keyboard([[
                ('غیرفعال‌کردن' if row.enabled else 'فعال‌کردن', f"adm:{'disable' if row.enabled else 'enable'}:{row.id}:{row.version}"),
                ('حذف', f'adm:delete:{row.id}:{row.version}')]]), parse_mode=None)


async def verify(bot, channel):
    try:
        chat = await bot.get_chat(channel.chat_id, request_timeout=5)
        member = await bot.get_chat_member(chat.id, bot.id, request_timeout=5)
        return chat.type == "channel" and member.status in {"administrator", "creator"}
    except Exception:
        return False


@router.message(AdminFlow.channel)
async def add_channel(message, state, bot):
    parts = (message.text or "").split()
    try:
        if not 1 <= len(parts) <= 2:
            raise ValueError()
        channel = required_channels(parts[0], parts[1] if len(parts) == 2 else "")[0]
    except (ValueError, IndexError):
        return await message.answer("قالب معتبر نیست؛ شناسهٔ عمومی یا شناسهٔ عددی همراه لینک دعوت معتبر بفرست.")
    if not await verify(bot, channel):
        return await message.answer("نوع مقصد یا ادمین‌بودن بات تأیید نشد. تنظیم دسترسی و شناسه را بررسی کن.")
    # Canonical ID prevents duplicates via public username vs numeric ID.
    chat = await bot.get_chat(channel.chat_id, request_timeout=5)
    channel = required_channels(str(chat.id), channel.join_url)[0]
    # The one-time env import may still use a public username; compare its
    # Telegram identity too, rather than registering the same destination twice.
    for existing in await admin.listing():
        if existing.chat_id.startswith("@"):
            old_chat = await bot.get_chat(existing.chat_id, request_timeout=5)
            if old_chat.id == chat.id:
                await state.clear()
                return await message.answer("این مورد قبلاً ثبت شده؛ از فهرست آن را مدیریت کن.",
                    reply_markup=keyboard([[('فهرست', 'adm:list')]]))
    added = await admin.mutate(message.from_user.id, "add", channel=channel)
    await state.clear()
    await message.answer("به شرط عضویت در کانال‌ها اضافه شد." if added else "مورد تکراری است یا ظرفیت فهرست تکمیل شده.",
                         reply_markup=keyboard([[('بازگشت به فهرست', 'adm:list')]]))


async def show_broadcast(message, actor, job_id):
    result = await broadcast.report(actor, job_id)
    if result is None:
        return await message.answer("گزارش پیدا نشد یا دورهٔ نگهداری آن پایان یافته است.")
    job, counts = result
    labels = {"draft": "در انتظار تأیید", "queued": "در صف ارسال", "stopped": "متوقف", "completed": "پایان‌یافته", "expired": "منقضی"}
    text = (f"ارسال سراسری #{job.id} — {labels.get(job.state, job.state)}\n"
            f"مخاطبان باقی‌مانده در گزارش: {sum(counts.values())}\n"
            f"موفق: {counts.get('sent', 0)} | مسدود/غیرقابل دسترس: {counts.get('blocked', 0)}\n"
            f"ناموفق: {counts.get('failed', 0)} | نامطمئن (بدون تکرار): {counts.get('unknown', 0)}\n"
            f"در انتظار: {counts.get('pending', 0)} | در حال ارسال/ثبت نتیجه: {counts.get('sending', 0)}\n"
            f"ردشده/لغوشده: {counts.get('skipped', 0)}\n"
            "توقف، پیام‌های ارسال‌شده یا درخواست شبکه‌ای آغازشده را پس نمی‌گیرد.")
    rows = [[('تازه‌سازی گزارش', f'adm:bcreport:{job.id}')], [('نمایش پیش‌نمایش', f'adm:bcpreview:{job.id}')]]
    if job.state == "draft" and job.admin_id == actor:
        text += "\nپیش‌نمایش را بررسی کن؛ تأیید، ارسال به فهرست ثابت همین پیام را آغاز می‌کند."
        rows.append([('تأیید نهایی و شروع ارسال', f'adm:bcconfirm:{job.id}')])
    if job.state in {"draft", "queued"}:
        rows.append([('توقف / لغو ادامه', f'adm:bcstop:{job.id}')])
    rows.append([('بازگشت', 'adm:bc')])
    await message.answer(text, reply_markup=keyboard(rows), parse_mode=None)


@router.message(AdminFlow.broadcast)
async def prepare_broadcast(message, state, bot):
    if not broadcast.supported(message):
        return await message.answer("این نوع پیام پشتیبانی نمی‌شود. متن یا یک عکس/ویدیو/فایل/صوت/ویس/انیمیشن/ویدیومسیج/استیکر مستقل بفرست؛ آلبوم و محتوای محافظت‌شده مجاز نیست.")
    preview = await bot.copy_message(chat_id=message.from_user.id, from_chat_id=message.chat.id,
                                     message_id=message.message_id, request_timeout=20)
    job_id = await broadcast.create(message.from_user.id, preview.message_id)
    await state.clear()
    await show_broadcast(message, message.from_user.id, job_id)
