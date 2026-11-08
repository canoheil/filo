"""Private, owner-scoped inline navigation. No message content in callback data."""
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.config import get_settings
from services import store, ai, persona as persona_service
from services.persona_health import DEFAULTS, is_question_only, display_value
from services.quota import utc_today

router = Router()
router.message.filter(F.chat.type == "private")


class EditPersona(StatesGroup):
    value = State()
    sandbox = State()


FIELDS = {
    "name": ("نام دستیار", "مثلاً: سارا", 120),
    "role": ("نقش", "مثلاً: دستیار فروش فروشگاه من", 255),
    "tone": ("لحن", "مثلاً: صمیمی، کوتاه و محترمانه", 120),
    "knowledge_base": ("اطلاعات و دانش", "قیمت‌ها، ساعات کاری و پاسخ پرسش‌های رایج را بنویس.", 12000),
    "instructions": ("قوانین پاسخ‌دهی", "مثلاً: دربارهٔ تخفیف قول نده؛ مشتری را به من ارجاع بده.", 12000),
    "signature": ("امضا", "متن امضای انتهای پاسخ را بنویس.", 120),
}


def button(label, action, style=None):
    return InlineKeyboardButton(text=label, callback_data=f"ui:{action}", style=style)


def keyboard(*rows):
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def home_keyboard():
    return keyboard(
        [button("🌱 آماده‌سازی فیلو؛ شروع / ادامه", "setup:overview", "primary")],
        [button("🎭 شخصیت دستیار", "persona"), button("📚 اطلاعات و قوانین", "knowledge")],
        [button("⚙️ شیوهٔ پاسخ‌دهی", "mode", "primary"), button("✍️ پیش‌نویس‌ها", "drafts")],
        [button("🔗 اتصال تلگرام", "connection"), button("❓ راهنما", "help")],
        [button("🧪 آزمایش پاسخ", "sandbox", "primary"), button("🔒 حریم خصوصی", "privacy")],
    )


def back():
    return [button("‹ منوی اصلی", "home")]


async def home(owner_id):
    user = await store.get_or_create_user(owner_id, None)
    p = await store.get_or_create_persona(owner_id)
    used = user.quota_used if user.quota_date == utc_today() else 0
    quota = f"{used} از {get_settings().FREE_DAILY_LIMIT} درخواست امروز" if user.plan == "free" else "بدون سقف روزانه در پلن فعلی"
    mode = "خودکار" if p.auto_reply else "پیش‌نویس با تأیید تو"
    from handlers.setup import overview
    checklist = await overview(owner_id)
    return (f"فیلو | دستیار تلگرام تو\n\nنام: {display_value(p.name)}\n"
            f"وضعیت: {'⏸ مکث' if p.is_paused else '▶️ فعال'}\n"
            f"پاسخ‌دهی: {mode}\nپلن: {user.plan}\nسهمیه: {quota}\n\n"
            f"{checklist}\n\nاز دکمهٔ آماده‌سازی برای شروع یا ادامه استفاده کن.")


@router.message(Command("start", "menu", "cancel"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(await home(message.from_user.id), reply_markup=home_keyboard(), parse_mode=None)


async def show(cb, text, markup):
    try:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message.lower():
            raise


@router.callback_query(F.data.startswith("ui:"))
async def navigate(cb: CallbackQuery, state: FSMContext):
    # Inline panels must only operate in the owner's private bot chat.
    if (not isinstance(cb.message, Message) or cb.message.chat.type != "private"
            or cb.message.chat.id != cb.from_user.id):
        await cb.answer("این منو فقط در گفتگوی خصوصی خودت در دسترس است.", show_alert=True)
        return
    action = (cb.data or "")[3:]
    pending = (await state.get_data()).get("confirmation")
    if action in {"autoconfirm", "forgetconfirm", "setup:start"} or action.startswith(("clear:", "repair:")):
        if pending != action:
            await cb.answer("این تأیید منقضی شده؛ دوباره از منو شروع کن.", show_alert=True)
            return
    await cb.answer()
    await state.clear()  # navigation always cancels an unfinished text edit
    uid = cb.from_user.id
    if action.startswith("setup:"):
        from handlers.setup import navigate_setup
        await navigate_setup(cb, state, action)
    elif action == "home":
        await show(cb, await home(uid), home_keyboard())
    elif action in {"persona", "knowledge"}:
        p = await store.get_or_create_persona(uid)
        fields = ["name", "role", "tone", "signature"] if action == "persona" else ["knowledge_base", "instructions"]
        rows = [[button("✏️ " + FIELDS[f][0], f"edit:{f}")] for f in fields]
        if action == "persona":
            for field in DEFAULTS:
                if is_question_only(getattr(p, field)):
                    rows.append([button(f"اصلاح {FIELDS[field][0]} آسیب‌دیده", f"repairask:{field}")])
            rows.append([button("🏪 کسب‌وکار", "use:business"), button("🧑 شخصی", "use:personal")])
        summary = (f"نام: {display_value(p.name)}\nنقش: {display_value(p.role)}\nلحن: {display_value(p.tone)}\nامضا: {p.signature or 'بدون امضا'}"
                   if action == "persona" else f"اطلاعات: {len(p.knowledge_base)} کاراکتر\nقوانین: {len(p.instructions)} کاراکتر")
        await show(cb, summary + "\n\nبرای ویرایش، یک مورد را انتخاب کن.", keyboard(*rows, back()))
    elif action.startswith("repairask:") and action[10:] in DEFAULTS:
        field = action[10:]
        await state.update_data(confirmation=f"repair:{field}")
        await show(cb, f"حروف قبلی قابل بازیابی نیستند. {FIELDS[field][0]} به «{DEFAULTS[field]}» بازنشانی شود؟\n"
                   "می‌توانی به‌جای بازنشانی، از بخش ویرایش مقدار دلخواهت را بنویسی.\n"
                   "اگر مقدار جدید هم خراب شد، تنظیم charset دیتابیس هنوز نیازمند بررسی است.", keyboard(
                       [button("تأیید بازنشانی", f"repair:{field}", "success")], [button("انصراف", "persona")]))
    elif action.startswith("repair:") and action[7:] in DEFAULTS:
        repaired = await store.reset_damaged_persona_field(uid, action[7:])
        await show(cb, "مقدار پیش‌فرض ثبت شد." if repaired else "تغییری انجام نشد؛ مقدار سالم است یا قبلاً تغییر کرده.",
                   keyboard([button("شخصیت دستیار", "persona")], back()))
    elif action == "sandbox":
        await state.set_state(EditPersona.sandbox)
        await show(cb, "آزمایش پاسخ فیلو\n\nیک پیام فرضی از مخاطب بنویس. پاسخ فقط همین‌جا نمایش داده می‌شود؛ "
                   "برای هیچ مخاطبی ارسال و به‌عنوان پیش‌نویس ذخیره نمی‌شود.\n"
                   "متن برای پردازش به سرویس AI می‌رود و هر آزمایش یک سهمیه مصرف می‌کند، حتی در صورت خطا.\n"
                   "از اطلاعات حساس استفاده نکن. حداکثر ۲۰۰۰ کاراکتر؛ برای انصراف /cancel.",
                   keyboard([button("انصراف", "home")]))
    elif action.startswith("edit:") and action[5:] in FIELDS:
        field = action[5:]
        title, hint, limit = FIELDS[field]
        p = await store.get_or_create_persona(uid)
        await state.set_state(EditPersona.value)
        await state.update_data(field=field)
        rows = [[button("انصراف", "home")]]
        if field in {"knowledge_base", "instructions", "signature"}:
            rows.insert(0, [button("پاک‌کردن این فیلد", f"clearask:{field}", "danger")])
        current = getattr(p, field) or "ثبت نشده"
        await show(cb, f"ویرایش {title}\n\nمقدار فعلی:\n{current[:1200]}\n\n{hint}\n"
                   f"متن جدید را در یک پیام بفرست (حداکثر {limit} کاراکتر). مقدار قبلی جایگزین می‌شود.\n"
                   "برای لغو /cancel یا دکمهٔ انصراف را بزن.", keyboard(*rows))
    elif action.startswith("clearask:") and action[9:] in {"knowledge_base", "instructions", "signature"}:
        field = action[9:]
        await state.update_data(confirmation=f"clear:{field}")
        await show(cb, f"{FIELDS[field][0]} پاک شود؟", keyboard(
            [button("بله، پاک کن", f"clear:{field}", "danger")], [button("انصراف", "home")]))
    elif action.startswith("clear:") and action[6:] in {"knowledge_base", "instructions", "signature"}:
        await store.update_persona(uid, **{action[6:]: ""})
        await show(cb, "فیلد پاک شد.\n\n" + await home(uid), home_keyboard())
    elif action.startswith("use:") and action[4:] in {"business", "personal"}:
        await store.get_or_create_user(uid, None, use_case=action[4:])
        await show(cb, "نوع استفاده ثبت شد؛ این انتخاب اطلاعات فعلی را تغییر نمی‌دهد.", keyboard(back()))
    elif action == "mode":
        p = await store.get_or_create_persona(uid)
        await show(cb, "شیوهٔ پاسخ‌دهی\n\nپیش‌نویس: ارسال فقط با تأیید تو.\n"
                   "خودکار: بدون تأیید از طرف حسابت پیام می‌فرستد.\n"
                   f"حالت فعلی: {'خودکار' if p.auto_reply else 'پیش‌نویس'}\n"
                   f"وضعیت: {'مکث' if p.is_paused else 'فعال'}", keyboard(
                       [button("پیش‌نویس (پیشنهادی)", "manual", "primary"), button("ارسال خودکار", "autoask")],
                       [button("▶️ ادامه" if p.is_paused else "⏸ مکث", "resume" if p.is_paused else "pause",
                               "success" if p.is_paused else None)], back()))
    elif action == "autoask":
        await state.update_data(confirmation="autoconfirm")
        await show(cb, "روشن‌کردن پاسخ خودکار\n\nبات بدون تأیید از طرف تو پاسخ می‌دهد. "
                   "اطلاعات و قوانین را بررسی کرده‌ای؟ این انتخاب مکث را لغو نمی‌کند.", keyboard(
                       [button("تأیید و فعال‌کردن", "autoconfirm", "success")], [button("انصراف", "mode")]))
    elif action in {"manual", "autoconfirm", "pause", "resume"}:
        values = {"manual": {"auto_reply": False}, "autoconfirm": {"auto_reply": True},
                  "pause": {"is_paused": True}, "resume": {"is_paused": False}}
        await store.update_persona(uid, **values[action])
        await show(cb, "تنظیمات ذخیره شد.\n\n" + await home(uid), home_keyboard())
    elif action == "drafts":
        # Callback message is from the bot; do not derive owner from its from_user.
        await render_drafts(cb, uid)
    elif action == "connection":
        connections = await store.list_user_connections(uid)
        enabled = sum(c.is_enabled for c in connections)
        await show(cb, f"اتصال‌های فعال ثبت‌شده: {enabled}\n\n"
                   "در تلگرام به Settings → Account/Profile → Chat Automation برو، "
                   "بات را انتخاب کن و دسترسی پاسخ را بده. نام مسیر ممکن است با نسخهٔ تلگرام فرق کند.\n"
                   "اگر هنوز به بات وصل نیستی، فعال‌بودن دستیار به‌تنهایی به معنی اتصال نیست.", keyboard(back()))
    elif action == "help":
        await show(cb, "شروع در سه قدم\n\n۱. شخصیت، اطلاعات و قوانین را تکمیل کن.\n"
                   "۲. حساب آزمایشی را از بخش اتصال وصل کن.\n۳. با حالت پیش‌نویس پاسخ‌ها را بررسی کن.\n\n"
                   "مکث، پاسخ‌های در حال ارسال را پس نمی‌گیرد. سهمیه بابت درخواست AI است؛ خطای AI هم سهمیه مصرف می‌کند.\n"
                   "کامندهای قبلی همچنان کار می‌کنند؛ /help فهرست آن‌هاست. /menu همیشه منو را باز می‌کند.", keyboard(back()))
    elif action == "privacy":
        await show(cb, "حریم خصوصی\n\nمتن ورودی برای تولید پاسخ به سرویس AI ارسال می‌شود، "
                   "اما به‌عنوان تاریخچهٔ گفتگو در دیتابیس ما ذخیره نمی‌شود. "
                   "پیش‌نویس‌ها تا تصمیم تو یا بررسی خطای ارسال ممکن است نگه داشته شوند.\n"
                   "حذف داده‌ها، پیام‌های قبلی تلگرام یا اطلاعات سرویس AI را پاک نمی‌کند.", keyboard(
                       [button("حذف داده‌های من…", "forgetask", "danger")], back()))
    elif action == "forgetask":
        await state.update_data(confirmation="forgetconfirm")
        await show(cb, "حذف تمام داده‌های من؟\n\nشخصیت، تنظیمات، اتصال‌های ثبت‌شده و پیش‌نویس‌ها حذف می‌شوند. "
                   "این کار قابل بازگشت نیست. برای جلوگیری از دریافت مجدد آپدیت‌ها، اتصال بات را در تنظیمات تلگرام هم قطع کن.", keyboard(
                       [button("بله، داده‌هایم حذف شود", "forgetconfirm", "danger")], [button("انصراف", "home")]))
    elif action == "forgetconfirm":
        await store.delete_user_data(uid)
        await show(cb, "داده‌هایت حذف شد. برای شروع دوباره /start را بزن.", None)
    else:
        await show(cb, "این گزینه معتبر نیست. از منوی اصلی ادامه بده.", home_keyboard())


async def render_drafts(cb, uid):
    rows = await store.list_open_drafts(uid)
    await show(cb, "پیش‌نویس‌های باز\n\n" + ("آخرین ۲۰ مورد در پیام‌های زیر نمایش داده می‌شوند."
               if rows else "پیش‌نویس بازی نداری."), keyboard([button("تازه‌سازی", "drafts")], back()))
    labels = {"draft": "در انتظار تأیید", "sending": "نیازمند بررسی ارسال",
              "blocked": "ارسال مسدود", "uncertain": "ارسال نامشخص؛ چت مقصد را بررسی کن"}
    for row in rows:
        markup = None
        if row.status == "draft":
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ ارسال", callback_data=f"draft:approve:{row.id}", style="success"),
                InlineKeyboardButton(text="✖ رد", callback_data=f"draft:reject:{row.id}", style="danger")]])
        await cb.message.answer(f"#{row.id} — {labels[row.status]}\n" +
                                ((row.draft_text or "")[:1800] if row.status == "draft" else ""),
                                reply_markup=markup, parse_mode=None)


@router.message(EditPersona.value, F.text, ~F.text.startswith("/"))
async def save_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("field")
    if field not in FIELDS:
        await state.clear()
        await message.answer("ویرایش منقضی شده؛ /menu را باز کن.")
        return
    title, _, limit = FIELDS[field]
    value = message.text.strip()
    if not value or len(value) > limit or len(value.encode("utf-8")) > 60000:
        await message.answer(f"متن معتبر بفرست؛ حداکثر {limit} کاراکتر. برای لغو /cancel.")
        return
    await store.update_persona(message.from_user.id, **{field: value})
    await state.clear()
    await message.answer(f"✓ {title} ذخیره شد.", reply_markup=home_keyboard(), parse_mode=None)


@router.message(EditPersona.value, ~F.text)
async def nontext_edit(message: Message):
    await message.answer("برای این تنظیم یک پیام متنی بفرست؛ برای لغو /cancel را بزن.")


@router.message(EditPersona.sandbox, F.text, ~F.text.startswith("/"))
async def sandbox_reply(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text or len(text) > 2000:
        await message.answer("یک پیام متنی بین ۱ تا ۲۰۰۰ کاراکتر بفرست یا /cancel را بزن.")
        return
    owner_id = message.from_user.id
    user = await store.get_or_create_user(owner_id, None)
    p = await store.get_or_create_persona(owner_id)
    if any(is_question_only(getattr(p, field)) for field in DEFAULTS):
        await state.clear()
        await message.answer("اول نام، نقش و لحن آسیب‌دیده را در شخصیت دستیار اصلاح کن؛ سپس آزمایش را شروع کن.",
                             reply_markup=keyboard([button("اصلاح شخصیت", "persona")], back()))
        return
    allowed, _ = await store.quota_hit_and_count(owner_id, get_settings().FREE_DAILY_LIMIT)
    await state.clear()
    if not allowed:
        await message.answer("سهمیهٔ امروز تمام شده؛ آزمایش به سرویس AI ارسال نشد.", reply_markup=home_keyboard())
        return
    from services.setup import fingerprint
    request_hash = fingerprint(p, user.use_case)
    try:
        reply = await ai.generate_reply(persona_service.build_system_prompt(p), text)
    except Exception:
        await message.answer("آزمایش پاسخ ناموفق بود؛ کمی بعد دوباره امتحان کن. سهمیهٔ این درخواست مصرف شد.",
                             reply_markup=home_keyboard())
        return
    await store.record_setup_test(owner_id, request_hash)
    await message.answer("🧪 پاسخ آزمایشی — به مخاطبی ارسال نشده:", parse_mode=None)
    for offset in range(0, len(reply), 1800):
        await message.answer(reply[offset:offset + 1800], parse_mode=None)
    await message.answer("برای اصلاح پاسخ، اطلاعات یا قوانین را ویرایش کن.", reply_markup=keyboard(
        [button("پاسخ مناسبه؛ ادامه", "setup:approve", "success")],
        [button("اصلاح اطلاعات / قوانین", "knowledge"), button("اصلاح لحن", "edit:tone")],
        [button("آزمایش دوباره", "sandbox", "primary")], back()))


@router.message(EditPersona.sandbox, ~F.text)
async def sandbox_nontext(message: Message):
    await message.answer("برای آزمایش، یک پیام متنی بفرست؛ برای انصراف /cancel.")
