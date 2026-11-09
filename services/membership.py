"""Telegram-authoritative membership; technical failures never mean non-membership."""
from collections import OrderedDict
from dataclasses import dataclass
import logging
import time
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from core.config import get_settings
from core.membership import RequiredChannel, ChannelPolicy


@dataclass(frozen=True)
class MembershipResult:
    state: str
    channels: tuple[RequiredChannel, ...] = ()


class MembershipService:
    def __init__(self, ttl=30, max_entries=1024, clock=time.monotonic):
        self.ttl, self.max_entries, self.clock = ttl, max_entries, clock
        self._positive = OrderedDict()
        self.channel_loader = None

    def clear_user(self, user_id):
        for key in list(self._positive):
            if key[1] == user_id:
                del self._positive[key]

    async def check(self, bot, user_id, *, fresh=False):
        try:
            policy = await self.channel_loader() if self.channel_loader else get_settings().required_channels
            channels = policy.channels if isinstance(policy, ChannelPolicy) else policy
            revision = policy.revision if isinstance(policy, ChannelPolicy) else 0
        except Exception as exc:
            logging.getLogger("secretary").warning("membership policy unavailable type=%s", type(exc).__name__)
            return MembershipResult("unavailable")
        if not channels:
            return MembershipResult("member")
        now = self.clock()
        for key, expires in list(self._positive.items()):
            if expires <= now:
                del self._positive[key]
        key = (bot.id, user_id, channels, revision)
        if not fresh and key in self._positive:
            self._positive.move_to_end(key)
            return MembershipResult("member", channels)
        self._positive.pop(key, None)
        missing, unknown = False, False
        for channel in channels:
            try:
                admin = await bot.get_chat_member(chat_id=channel.chat_id, user_id=bot.id, request_timeout=5)
                if admin.status not in {"administrator", "creator"}:
                    unknown = True
                    logging.getLogger("secretary").warning("membership bot admin permission missing")
                    continue
                member = await bot.get_chat_member(chat_id=channel.chat_id, user_id=user_id, request_timeout=5)
                if member.status in {"member", "administrator", "creator"}:
                    continue
                if member.status == "restricted" and member.is_member:
                    continue
                if member.status in {"left", "kicked", "restricted"}:
                    missing = True
                else:
                    unknown = True
            except Exception as exc:
                unknown = True
                logging.getLogger("secretary").warning("membership check failed type=%s", type(exc).__name__)
        state = "unavailable" if unknown else "missing" if missing else "member"
        if state == "member":
            self._positive[key] = self.clock() + self.ttl
            while len(self._positive) > self.max_entries:
                self._positive.popitem(last=False)
        return MembershipResult(state, channels)


membership = MembershipService()


def prompt(result):
    if result.state == "unavailable":
        text = ("بررسی عضویت در کانال‌ها فعلاً به‌دلیل مشکل فنی ممکن نیست. "
                "این پیام به معنی عضو نبودن تو نیست؛ کمی بعد «بررسی دوباره» را بزن.")
    else:
        text = ("برای ادامهٔ استفاده از فیلو، باید عضو کانال‌های زیر شوی.\n\n"
                "بعد از عضویت، «عضو شدم؛ بررسی دوباره» را بزن. "
                "اگر درخواست عضویت فرستاده‌ای، باید ابتدا تأیید شود.")
    rows = [[InlineKeyboardButton(text="عضویت در کانال‌ها" + (f" · {i}" if len(result.channels) > 1 else ""),
                                  url=channel.join_url)] for i, channel in enumerate(result.channels, 1)]
    rows.append([InlineKeyboardButton(text="عضو شدم؛ بررسی دوباره", callback_data="membership:check", style="primary")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
