"""Resumable wizard: exiting never overwrites existing settings."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from services import store
from services.setup import STEPS, fingerprint, readiness
from services.persona_health import is_question_only

router = Router()
router.message.filter(F.chat.type == "private")


class SetupInput(StatesGroup):
    value = State()


async def overview(owner_id):
    user = await store.get_or_create_user(owner_id, None)
    p = await store.get_or_create_persona(owner_id)
    progress = await store.get_setup_progress(owner_id)
    connections = await store.list_user_connections(owner_id)
    checks = readiness(p, user, progress, connections)
    return "آماده‌سازی فیلو\n\n" + "\n".join(
        f"{'✅' if ok else '▫️'} {label}" for label, ok in checks.items()) + (
        "\n\nاین چک‌لیست تضمین کیفیت نیست. وضعیت اتصال، آخرین وضعیت ثبت‌شده است؛ پیش از شروع بررسی زنده را بزن.")


async def next_step(cb, state):
    from handlers.menu import FIELDS, button, keyboard, show, back
    uid = cb.from_user.id
    progress = await store.get_setup_progress(uid)
    step = next((step for step in STEPS if step not in progress.completed), None)
    if step is None:
        await show(cb, await overview(uid), keyboard(
            [button("🧪 آزمایش پاسخ", "sandbox", "primary")],
            [button("🔗 بررسی اتصال و شروع امن", "setup:connection")],
            [button("بازبینی مرحله‌ها", "setup:review")], back()))
        return
    await prompt(cb, state, step)


async def prompt(cb, state, step):
    from handlers.menu import FIELDS, button, keyboard, show
    uid = cb.from_user.id
    index = STEPS.index(step) + 1
    if step == "use_case":
        user = await store.get_or_create_user(uid, None)
        await show(cb, f"قدم {index} از ۶ — نوع استفاده\nفعلی: {'شخصی' if user.use_case == 'personal' else 'کسب‌وکار'}\n"
                   "این انتخاب نقش و اطلاعات فعلی را عوض نمی‌کند.", keyboard(
                       [button("🏪 کسب‌وکار", "setup:use:business"), button("🧑 شخصی", "setup:use:personal")],
                       [button("همین انتخاب بماند", "setup:keep:use_case")],
                       [button("خروج؛ بعداً ادامه می‌دهم", "home")]))
        return
    p = await store.get_or_create_persona(uid)
    title, hint, limit = FIELDS[step]
    await state.set_state(SetupInput.value)
    await state.update_data(setup_field=step)
    await show(cb, f"قدم {index} از ۶ — {title}\n\nفعلی: {(getattr(p, step) or 'ثبت نشده')[:1000]}\n\n"
               f"{hint}\nمتن جدید را بفرست (حداکثر {limit} کاراکتر). فقط همین فیلد جایگزین می‌شود.\n"
               "برای حفظ مقدار فعلی دکمهٔ زیر را بزن؛ خالی‌بودن اطلاعات در چک‌لیست مشخص می‌ماند.", keyboard(
                   [button("حفظ مقدار فعلی و ادامه", f"setup:keep:{step}")],
                   [button("خروج؛ بعداً ادامه می‌دهم", "home")]))


async def navigate_setup(cb, state, action):
    from handlers.menu import button, keyboard, show, back
    uid = cb.from_user.id
    if action == "setup:overview":
        await show(cb, await overview(uid), keyboard(
            [button("ادامهٔ راه‌اندازی", "setup:continue", "primary")],
            [button("شخصیت", "persona"), button("اطلاعات و قوانین", "knowledge")],
            [button("آزمایش پاسخ", "sandbox"), button("بررسی اتصال", "setup:connection")], back()))
    elif action == "setup:continue":
        await next_step(cb, state)
    elif action == "setup:review":
        # Review one chosen field without resetting durable progress or existing values.
        from handlers.menu import FIELDS
        await show(cb, "کدام مرحله را بازبینی می‌کنی؟", keyboard(
            *[[button("نوع استفاده" if step == "use_case" else FIELDS[step][0], f"setup:step:{step}")] for step in STEPS], back()))
    elif action.startswith("setup:step:") and action[11:] in STEPS:
        await prompt(cb, state, action[11:])
    elif action.startswith("setup:use:") and action[10:] in {"business", "personal"}:
        await store.get_or_create_user(uid, None, use_case=action[10:])
        await store.mark_setup_step(uid, "use_case")
        await next_step(cb, state)
    elif action.startswith("setup:keep:") and action[11:] in STEPS:
        step = action[11:]
        p = await store.get_or_create_persona(uid)
        if step != "use_case" and is_question_only(getattr(p, step)):
            await show(cb, "مقدار آسیب‌دیده را با ویرایش یا بازنشانی در بخش شخصیت اصلاح کن.", keyboard(
                [button("شخصیت دستیار", "persona")], back()))
            return
        await store.mark_setup_step(uid, step)
        await next_step(cb, state)
    elif action == "setup:approve":
        user = await store.get_or_create_user(uid, None)
        p = await store.get_or_create_persona(uid)
        accepted = await store.approve_setup_test(uid, fingerprint(p, user.use_case))
        await show(cb, "نظرت ثبت شد؛ حالا اتصال را بررسی کن." if accepted else
                   "تنظیمات تغییر کرده یا آزمایش موفقی ثبت نشده؛ دوباره آزمایش کن.", keyboard(
                       [button("بررسی اتصال", "setup:connection"), button("آزمایش دوباره", "sandbox")], back()))
    elif action == "setup:connection":
        connections = await store.list_user_connections(uid)
        live = False
        failed = False
        for connection in connections:
            if not connection.is_enabled:
                continue
            try:
                result = await cb.bot.get_business_connection(connection.connection_id)
                if result.user.id != uid:
                    continue
                await store.upsert_connection(result.id, uid, result.rights.model_dump() if result.rights else None,
                                              result.user_chat_id, result.is_enabled)
                live = live or bool(result.is_enabled and result.rights and result.rights.can_reply)
            except Exception:
                failed = True
        text = ("اتصال و مجوز پاسخ همین حالا تأیید شد.\nشروع با پیش‌نویس، حالت خودکار را خاموش می‌کند و مکث را برمی‌دارد."
                if live else "اتصال قابل پاسخ تأیید نشد. از راهنمای اتصال استفاده کن و دوباره بررسی کن.")
        if failed:
            text += "\nبررسی بعضی اتصال‌ها ناموفق بود؛ وضعیت آن‌ها قطعی نیست."
        if live:
            await state.update_data(confirmation="setup:start")
        await show(cb, text, keyboard(
            *([[button("شروع با پیش‌نویس امن", "setup:start", "success")]] if live else []),
            [button("راهنمای اتصال", "connection"), button("بررسی دوباره", "setup:connection")], back()))
    elif action == "setup:start":
        # Recheck after confirmation; permissions may have changed since the panel opened.
        live = False
        for connection in await store.list_user_connections(uid):
            if not connection.is_enabled:
                continue
            try:
                result = await cb.bot.get_business_connection(connection.connection_id)
                live = live or bool(result.user.id == uid and result.is_enabled
                                    and result.rights and result.rights.can_reply)
            except Exception:
                continue
        if not live:
            await show(cb, "مجوز پاسخ تأیید نشد؛ تنظیمات پاسخ‌دهی تغییر نکرد.", keyboard(
                [button("بررسی اتصال", "setup:connection")], back()))
            return
        # No message is sent here; existing delivery checks still apply to all drafts.
        await store.update_persona(uid, auto_reply=False, is_paused=False)
        await show(cb, "حالت پیش‌نویس فعال شد. یک پیام از حساب آزمایشی دیگر بفرست؛ پاسخ فقط پس از تأیید تو ارسال می‌شود.",
                   keyboard([button("پیش‌نویس‌ها", "drafts")], back()))
    else:
        await show(cb, "گزینه معتبر نیست؛ راه‌اندازی را دوباره باز کن.", keyboard(back()))


@router.message(SetupInput.value, F.text, ~F.text.startswith("/"))
async def save_step(message: Message, state: FSMContext):
    from handlers.menu import FIELDS, button, keyboard
    field = (await state.get_data()).get("setup_field")
    if field not in STEPS[1:]:
        await state.clear()
        await message.answer("این مرحله منقضی شده؛ /menu را باز کن.")
        return
    value = message.text.strip()
    limit = FIELDS[field][2]
    if not value or len(value) > limit or len(value.encode("utf-8")) > 60000 or is_question_only(value):
        await message.answer(f"متن سالم و غیرخالی تا {limit} کاراکتر بفرست؛ برای خروج /cancel.")
        return
    await store.update_persona(message.from_user.id, **{field: value})
    await store.mark_setup_step(message.from_user.id, field)
    await state.clear()
    await message.answer("✓ ذخیره شد؛ می‌توانی مرحلهٔ بعد را ادامه بدهی یا بعداً برگردی.", reply_markup=keyboard(
        [button("ادامهٔ راه‌اندازی", "setup:continue", "primary")], [button("منوی اصلی", "home")]))


@router.message(SetupInput.value, ~F.text)
async def nontext(message: Message):
    await message.answer("یک پیام متنی بفرست؛ برای خروج /cancel.")
