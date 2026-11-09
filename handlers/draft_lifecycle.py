"""Owner-scoped review/close controls. Never expose a resend action."""
from datetime import datetime
from core.draft_ui import valid_number
from services.draft_lifecycle import close_failed, reject_pending


async def navigate(cb, state, action, pending):
    from handlers.menu import button, keyboard, show, back
    uid = cb.from_user.id
    if action == "life:rejectask":
        await state.update_data(confirmation="life:rejectconfirm", before=datetime.utcnow().isoformat())
        await show(cb, "تمام پیش‌نویس‌های در انتظار فعلی رد و متن‌شان پاک شود؟\n"
                   "پیش‌نویس‌هایی که بعد از بازکردن این تأیید ساخته می‌شوند شامل این کار نیستند. "
                   "ارسال‌های درحال انجام و نامشخص تغییر نمی‌کنند.", keyboard(
                       [button("بله، همهٔ موارد فعلی رد شوند", "life:rejectconfirm", "danger")],
                       [button("انصراف", "drafts")]))
    elif action == "life:rejectconfirm":
        try:
            before = datetime.fromisoformat(pending["before"])
        except (KeyError, ValueError, TypeError):
            await show(cb, "تأیید منقضی شده؛ دوباره شروع کن.", keyboard(back()))
            return
        count = await reject_pending(uid, before)
        await show(cb, f"{count} پیش‌نویس رد شد؛ متن دیتابیس پاک شد.", keyboard([button("پیش‌نویس‌ها", "drafts")], back()))
    elif action.startswith("life:closeask:") and valid_number(action[14:]):
        draft_id = int(action[14:])
        await state.update_data(confirmation=f"life:close:{draft_id}")
        await show(cb, f"بستن مورد #{draft_id}\n\nاگر وضعیت ارسال نامشخص است، اول چت مقصد را بررسی کن. "
                   "بستن فقط این مورد را از فهرست باز خارج و متن محلی را پاک می‌کند؛ "
                   "به معنی ارسال‌شدن یا نرسیدن پیام نیست و چیزی را از تلگرام حذف نمی‌کند.", keyboard(
                       [button("بررسی کردم؛ مورد بسته شود", f"life:close:{draft_id}", "danger")],
                       [button("انصراف", "drafts")]))
    elif action.startswith("life:close:") and valid_number(action[11:]):
        closed = await close_failed(uid, int(action[11:]))
        await show(cb, "مورد بسته و متن محلی پاک شد." if closed else
                   "مورد در دسترس نیست یا هنوز ارسال در حال انجام است؛ تغییری نکرد.",
                   keyboard([button("پیش‌نویس‌ها", "drafts")], back()))
    else:
        await show(cb, "گزینه نامعتبر است.", keyboard(back()))
