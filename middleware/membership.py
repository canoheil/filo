"""Guard private product entrypoints, including stale buttons; keep erasure accessible."""
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from core.config import get_settings
from services.membership import membership, prompt

PRIVACY_CALLBACKS = {"ui:privacy", "ui:forgetask", "ui:forgetconfirm"}


def erasure_request(event):
    if isinstance(event, CallbackQuery):
        return event.data in PRIVACY_CALLBACKS
    if isinstance(event, Message) and event.text:
        command = event.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        return command == "/forget"
    return False


async def acknowledge(event, text="", **kwargs):
    try:
        await event.answer(text, **kwargs)
    except TelegramBadRequest:
        # An expired callback must not prevent sending the membership result.
        pass


class MembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        retry = isinstance(event, CallbackQuery) and event.data == "membership:check"
        from services.admin import authorized
        admin_entry = (isinstance(event, Message) and event.text and
                       event.text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/admin")
        admin_callback = isinstance(event, CallbackQuery) and (event.data or "").startswith("adm:")
        admin_state = data.get("state")
        current_state = await admin_state.get_state() if admin_state else None
        in_admin = (isinstance(current_state, str) and current_state.startswith("AdminFlow:")
                    and isinstance(event, Message) and not (event.text or "").startswith("/"))
        if ((admin_entry or admin_callback or in_admin) and event.from_user and authorized(event.from_user.id)) or erasure_request(event):
            return await handler(event, data)
        if membership.channel_loader is None and not get_settings().required_channels and not retry:
            return await handler(event, data)
        if isinstance(event, Message):
            if event.chat.type != "private" or event.from_user is None:
                return await handler(event, data)
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            if (not isinstance(event.message, Message) or event.message.chat.type != "private"
                    or event.message.chat.id != event.from_user.id):
                await event.answer("این گزینه فقط در چت خصوصی خودت در دسترس است.", show_alert=True)
                return
            user_id = event.from_user.id
        else:
            return await handler(event, data)
        if retry:
            await acknowledge(event, "در حال بررسی عضویت…")
        start = isinstance(event, Message) and bool(event.text) and event.text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/start"
        result = await membership.check(data["bot"], user_id, fresh=bool(start or retry))
        state = data.get("state")
        if result.state == "member":
            if retry:
                if state:
                    await state.clear()
                from handlers.menu import home, home_keyboard
                await event.message.answer(await home(user_id), reply_markup=home_keyboard(), parse_mode=None)
                return
            return await handler(event, data)
        if state:
            await state.clear()
        text, markup = prompt(result)
        if isinstance(event, CallbackQuery):
            if not retry:
                await acknowledge(event, "عضویت هنوز تأیید نشده." if result.state == "missing" else
                                   "بررسی عضویت فعلاً ممکن نیست.", show_alert=True)
            await event.message.answer(text, reply_markup=markup, parse_mode=None)
        else:
            await event.answer(text, reply_markup=markup, parse_mode=None)
