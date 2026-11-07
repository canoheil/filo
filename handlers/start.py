"""Onboarding + persona customization (the product's front door).
Persian commands; free-form persona fields — no niche lock-in."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import get_settings
from services import store

router = Router()

WELCOME = (
    "سلام! 👋\n"
    "من سکرتر هوشمند تلگرامم؛ می‌توانم از طرف تو و با اجازهٔ تو به پیام‌های خصوصی‌ات جواب بدهم.\n\n"
    "اول بگو برای چی می‌خواهی ازم استفاده کنی تا استایل پیش‌فرض را بچینم:"
)


def use_case_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏪 کسب‌وکارم", callback_data="uc:business"),
        InlineKeyboardButton(text="🧑 استفادهٔ شخصی", callback_data="uc:personal"),
    ]])


HELP = (
    "دستورات:\n"
    "/whoami — پروفایل فعلی سکرترت\n"
    "/setname <نام> — اسم سکرتر (مثلاً «سارا»)\n"
    "/setrole <نقش> — نقش (مثلاً «فروشندهٔ فروشگاه X» یا «دستیار شخصی من»)\n"
    "/settone <لحن> — لحن پاسخ\n"
    "/setinfo <متن> — اطلاعات/دانش سکرتر؛ هر چیزی که باید بداند (قیمت‌ها، ساعات کاری، FAQ…)\n"
    "/setrules <متن> — قوانین اختصاصی تو برای جواب دادن\n"
    "/setauto on|off — حالت ارسال مستقیم یا «پیش‌نویس برای تأیید» (پیش‌فرض)\n"
    "/pause و /resume — مکث و ادامه\n"
    "/forget — حذف کامل داده‌هایت\n\n"
    "برای اتصال: Settings → Account/Profile → Chat Automation → یوزرنیم من را وارد کن و دسترسی را بده."
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = await store.get_or_create_user(message.from_user.id, message.from_user.username)
    await store.get_or_create_persona(user.tg_user_id)
    await message.answer(WELCOME, reply_markup=use_case_kb())


@router.callback_query(F.data.startswith("uc:"))
async def cb_use_case(cb: CallbackQuery) -> None:
    use_case = cb.data.split(":", 1)[1]
    await store.get_or_create_user(cb.from_user.id, cb.from_user.username, use_case=use_case)
    label = "کسب‌وکاری" if use_case == "business" else "شخصی"
    await cb.message.edit_text(f"ثبت شد ✓ — استفادهٔ {label}.\n\nحالا با /setinfo اطلاعاتت را بده؛ بعد /setrules و اگر بی‌خطر بودی /setauto on.\n\n{HELP}")
    await cb.answer()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    user = await store.get_or_create_user(message.from_user.id, message.from_user.username)
    p = await store.get_or_create_persona(user.tg_user_id)
    mode = "🟢 خودکار (پیش‌نویس نمی‌گیرد)" if p.auto_reply else "🟡 پیش‌نویس برای تأیید"
    await message.answer(
        f"پلن: {user.plan} | حالت: {mode}\n"
        f"نام: {p.name}\nنقش: {p.role}\nلحن: {p.tone}\n"
        f"دانش: {len(p.knowledge_base)} کاراکتر | قوانین: {len(p.instructions)} کاراکتر\n"
        f"پاز: {'بله' if p.is_paused else 'خیر'}"
    )


@router.message(Command("setname"))
async def cmd_setname(message: Message) -> None:
    value = (message.text or "").split(maxsplit=1)[1:] or [""]
    if not value[0].strip():
        await message.answer("فرمت: /setname سارا")
        return
    await store.update_persona(message.from_user.id, name=value[0].strip()[:120])
    await message.answer("ثبت شد ✓")


@router.message(Command("setrole"))
async def cmd_setrole(message: Message) -> None:
    value = (message.text or "").split(maxsplit=1)[1:] or [""]
    if not value[0].strip():
        await message.answer("فرمت: /setrole فروشندهٔ فروشگاه X")
        return
    await store.update_persona(message.from_user.id, role=value[0].strip()[:255])
    await message.answer("ثبت شد ✓")


@router.message(Command("settone"))
async def cmd_settone(message: Message) -> None:
    value = (message.text or "").split(maxsplit=1)[1:] or [""]
    if not value[0].strip():
        await message.answer("فرمت: /settone مودب و صمیمی")
        return
    await store.update_persona(message.from_user.id, tone=value[0].strip()[:120])
    await message.answer("ثبت شد ✓")


@router.message(Command("setinfo"))
async def cmd_setinfo(message: Message) -> None:
    value = (message.text or "").split(maxsplit=1)[1:] or [""]
    if not value[0].strip():
        await message.answer("فرمت: /setinfo <متن کامل اطلاعات> — هر سطری که می‌خواهی سکرتر بداند")
        return
    await store.update_persona(message.from_user.id, knowledge_base=value[0].strip())
    await message.answer("ثبت شد ✓ — این اطلاعات مبنای جواب‌هاست.")


@router.message(Command("setrules"))
async def cmd_setrules(message: Message) -> None:
    value = (message.text or "").split(maxsplit=1)[1:] or [""]
    if not value[0].strip():
        await message.answer("فرمت: /setrules <قوانین اختصاصی تو>")
        return
    await store.update_persona(message.from_user.id, instructions=value[0].strip())
    await message.answer("ثبت شد ✓")


@router.message(Command("setauto"))
async def cmd_setauto(message: Message) -> None:
    arg = (message.text or "").split(maxsplit=1)[1:] or [""]
    flag = arg[0].strip().lower()
    if flag not in {"on", "off"}:
        await message.answer("فرمت: /setauto on  یا  /setauto off")
        return
    await store.update_persona(message.from_user.id, auto_reply=(flag == "on"))
    if flag == "on":
        await message.answer("🟢 حالت خودکار روشن شد. قبل از روشن کردن مطمئن شو /setinfo دقیق است!")
    else:
        await message.answer("🟡 پیش‌نویس برای تأیید — امن است.")


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    await store.update_persona(message.from_user.id, is_paused=True)
    await message.answer("⏸ مکث شد — هیچ جوابی از طرف تو نمی‌دهم تا /resume.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    await store.update_persona(message.from_user.id, is_paused=False)
    await message.answer("▶️ ادامه می‌دهم.")


@router.message(Command("forget"))
async def cmd_forget(message: Message) -> None:
    await store.delete_user_data(message.from_user.id)
    await message.answer("داده‌هایت پاک شد. برای شروع دوباره /start.")
