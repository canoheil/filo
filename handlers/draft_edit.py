"""Editing never sends; only a new version-bound approval can send the result."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.draft_ui import draft_keyboard, valid_number
from services import store

router = Router()
router.message.filter(F.chat.type == "private")


class DraftEdit(StatesGroup):
    text = State()


@router.callback_query(F.data.startswith("draft:edit:"))
async def begin_edit(cb: CallbackQuery, state: FSMContext):
    if (not isinstance(cb.message, Message) or cb.message.chat.type != "private"
            or cb.message.chat.id != cb.from_user.id):
        await cb.answer("ویرایش فقط در چت خصوصی مالک ممکن است.", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 4 or not valid_number(parts[2]) or not valid_number(parts[3], zero=True):
        await cb.answer("درخواست نامعتبر است.", show_alert=True)
        return
    draft_id, revision = int(parts[2]), int(parts[3])
    row = await store.get_owned_pending_draft(draft_id, cb.from_user.id, revision)
    if row is None:
        await cb.answer("این نسخه تغییر کرده، بررسی شده یا منقضی است؛ پیش‌نویس‌ها را دوباره باز کن.", show_alert=True)
        return
    await cb.answer()
    await state.clear()
    await state.set_state(DraftEdit.text)
    await state.update_data(draft_id=draft_id, revision=revision)
    await cb.message.answer(f"ویرایش پیش‌نویس #{draft_id}\nمتن فعلی در پیام بعدی است. "
                            "متن کامل جایگزین را در یک پیام بفرست؛ ارسال خودکار انجام نمی‌شود. "
                            "برای انصراف /cancel یا دکمهٔ زیر را بزن.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="انصراف؛ حفظ متن قبلی", callback_data="ui:home")]]), parse_mode=None)
    await cb.message.answer(row.draft_text or "(متن خالی)", parse_mode=None)


@router.message(DraftEdit.text, F.text, ~F.text.startswith("/"))
async def save_edit(message: Message, state: FSMContext):
    value = message.text.strip()
    if not value or len(value.encode("utf-16-le")) // 2 > 4096:
        await message.answer("متن غیرخالی و حداکثر ۴۰۹۶ واحد متنی تلگرام بفرست؛ ایموجی ممکن است دو واحد باشد. برای لغو /cancel.")
        return
    data = await state.get_data()
    if "draft_id" not in data or "revision" not in data:
        await state.clear()
        await message.answer("ویرایش منقضی شده؛ /drafts را دوباره باز کن.")
        return
    draft_id, revision = data["draft_id"], data["revision"]
    saved = await store.edit_pending_draft(draft_id, message.from_user.id, revision, value)
    await state.clear()
    if not saved:
        await message.answer("ذخیره نشد؛ نسخه تغییر کرده یا پیش‌نویس قبلاً ارسال/رد شده. /drafts را بررسی کن.")
        return
    await message.answer(f"✓ نسخهٔ جدید پیش‌نویس #{draft_id} ذخیره شد؛ هنوز ارسال نشده. متن نهایی:", parse_mode=None)
    await message.answer(value, reply_markup=draft_keyboard(draft_id, revision + 1), parse_mode=None)


@router.message(DraftEdit.text, ~F.text)
async def nontext(message: Message):
    await message.answer("برای ویرایش یک پیام متنی بفرست؛ برای لغو /cancel.")
