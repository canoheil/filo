"""Single-process, bounded outbound admission at the aiogram session boundary.

Never retries an API call. Business and broadcast sends never wait in a queue.
"""
import asyncio
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timedelta
import math
import time
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter
from core.config import get_settings

broadcast_request = ContextVar("broadcast_request", default=False)


class OutboundDeferred(TelegramRetryAfter):
    """Local rejection BEFORE network; safe to distinguish from uncertain sends."""
    def __init__(self, method, seconds=1):
        super().__init__(method=method, message="Local outbound capacity unavailable", retry_after=max(1, math.ceil(seconds)))


class OutboundLimiter(BaseRequestMiddleware):
    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep, settings=None):
        self.clock, self.sleep = clock, sleep
        self.settings = settings
        self.lock = asyncio.Lock()
        self.chats = OrderedDict()
        self.next_global = 0.0
        self.blocked_until = 0.0
        self.inflight = 0
        self.waiters = 0
        self.product_waiters = 0

    def cfg(self):
        return self.settings or get_settings()

    def purge(self):
        now = self.clock()
        # Bulk methods reserve multiple slots, so expiry order can differ
        # from insertion order. The scan itself is bounded by MAX_CHATS.
        for key, last_slot in list(self.chats.items()):
            if last_slot + 60 <= now:
                del self.chats[key]

    def remaining(self):
        return max(0, self.blocked_until - self.clock())

    async def flood(self, seconds):
        self.blocked_until = max(self.blocked_until, self.clock() + max(1, seconds))
        # Only flood events touch DB, never ordinary successful requests.
        from database.connection import get_session
        from database.models import OutboundCooldown
        async with get_session() as s:
            row = await s.get(OutboundCooldown, 1, with_for_update=True)
            until = datetime.utcnow() + timedelta(seconds=max(1, seconds))
            if row is None:
                s.add(OutboundCooldown(id=1, blocked_until=until))
            elif row.blocked_until < until:
                row.blocked_until = until
            await s.commit()

    async def restore(self):
        from database.connection import get_session
        from database.models import OutboundCooldown, BroadcastJob
        from sqlalchemy import select, func
        async with get_session() as s:
            row = await s.get(OutboundCooldown, 1)
            if row is None:
                row = OutboundCooldown(id=1, blocked_until=datetime.utcnow())
                s.add(row)
                await s.flush()
            old = await s.scalar(select(func.max(BroadcastJob.retry_at)))
            untils = [t for t in [row.blocked_until if row else None, old] if t is not None]
            if untils:
                seconds = max(0, (max(untils) - datetime.utcnow()).total_seconds())
                self.blocked_until = max(self.blocked_until, self.clock() + seconds)
            await s.commit()

    @staticmethod
    def kind(method):
        name = method.__api_method__
        creates = name.startswith(("send", "copy", "forward")) and getattr(method, "chat_id", None) is not None
        paced = creates or name.startswith("editMessage") or name == "answerCallbackQuery"
        return creates, paced

    async def admit(self, bot, method):
        creates, paced = self.kind(method)
        if not paced:
            return
        cfg = self.cfg()
        low = broadcast_request.get()
        immediate = low or bool(getattr(method, "business_connection_id", None)) or method.__api_method__ == "answerCallbackQuery"
        cost = len(getattr(method, "media", None) or getattr(method, "message_ids", None) or [None]) if creates else 1
        cost = max(1, cost)
        registered = False
        start = self.clock()
        try:
            while True:
                async with self.lock:
                    self.purge()
                    now = self.clock()
                    chat = getattr(method, "chat_id", None)
                    key = (bot.id, str(chat)) if creates else None
                    group = isinstance(chat, int) and chat < 0 or isinstance(chat, str) and (chat.startswith("@") or chat.startswith("-"))
                    interval = cfg.OUTBOUND_GROUP_INTERVAL_SECONDS if group else cfg.OUTBOUND_CHAT_INTERVAL_SECONDS
                    delay = max(0, self.next_global - now, self.blocked_until - now)
                    if key in self.chats:
                        delay = max(delay, self.chats[key] + interval - now)
                    full = key is not None and key not in self.chats and len(self.chats) >= cfg.OUTBOUND_MAX_CHATS
                    busy = self.inflight >= cfg.OUTBOUND_MAX_INFLIGHT
                    priority = low and self.product_waiters > 0
                    if registered and now - start > cfg.OUTBOUND_MAX_WAIT_SECONDS:
                        raise OutboundDeferred(method, max(delay, 1))
                    if not full and not busy and not priority and delay <= 0:
                        self.next_global = now + cost * cfg.OUTBOUND_GLOBAL_INTERVAL_SECONDS
                        if key is not None:
                            self.chats[key] = now + (cost - 1) * interval
                            self.chats.move_to_end(key)
                        self.inflight += 1
                        return True
                    if immediate or full or now - start >= cfg.OUTBOUND_MAX_WAIT_SECONDS:
                        raise OutboundDeferred(method, max(delay, 1))
                    if not registered:
                        if self.waiters >= cfg.OUTBOUND_MAX_WAITERS:
                            raise OutboundDeferred(method)
                        self.waiters += 1
                        self.product_waiters += 1
                        registered = True
                await self.sleep(min(max(delay, .01), .1))
        finally:
            if registered:
                self.waiters -= 1
                self.product_waiters -= 1

    async def __call__(self, make_request, bot, method):
        reserved = await self.admit(bot, method)
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as exc:
            # Do not repeat, including explicit 429. Existing call sites retain
            # their own delivery state machine. Flood state is shared globally.
            try:
                await self.flood(exc.retry_after)
            except Exception as persist_error:
                import logging
                logging.getLogger("secretary").warning("outbound cooldown persistence failed type=%s", type(persist_error).__name__)
            raise
        finally:
            if reserved:
                self.inflight -= 1


outbound = OutboundLimiter()
