"""Version-bound draft actions; old buttons must not authorize unseen edits."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def draft_keyboard(draft_id: int, revision: int = 0):
    suffix = f":{revision}" if revision else ""  # legacy buttons mean revision zero
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ارسال", callback_data=f"draft:approve:{draft_id}{suffix}", style="success"),
        InlineKeyboardButton(text="✏️ ویرایش", callback_data=f"draft:edit:{draft_id}:{revision}", style="primary"),
        InlineKeyboardButton(text="✖ رد", callback_data=f"draft:reject:{draft_id}{suffix}", style="danger"),
    ]])


def valid_number(value: str, *, zero=False):
    return (value.isascii() and value.isdigit() and len(value) <= 19
            and (0 if zero else 1) <= int(value) <= 2**63 - 1)
