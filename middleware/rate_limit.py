"""Bounded single-process admission for ordinary messages and callbacks.

Not an outbound Telegram limiter or a business/AI concurrency guard.
"""
from services.protection import metrics
import asyncio
from collections import OrderedDict
import math
import time

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message

SAFETY_COMMANDS = {"/pause", "/cancel", "/forget"}
SAFETY_CALLBACKS = {"ui:pause", "ui:privacy", "ui:forgetask", "ui:forgetconfirm"}


def safety_request(event):
    if isinstance(event, CallbackQuery):
        return event.data in SAFETY_CALLBACKS
    if isinstance(event, Message) and event.text:
        command = event.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        return command in SAFETY_COMMANDS
    return False


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, limit_seconds=0.8, *, max_entries=4096, ttl_seconds=60,
                 clock=time.monotonic):
        if (not math.isfinite(limit_seconds) or limit_seconds <= 0
                or not math.isfinite(ttl_seconds) or ttl_seconds < limit_seconds
                or max_entries < 1):
            raise ValueError("Invalid admission limits")
        self._lock = asyncio.Lock()
        self._buckets = {False: OrderedDict(), True: OrderedDict()}
        self._limit = limit_seconds
        self._capacity = max_entries
        self._ttl = ttl_seconds
        self._clock = clock

    async def admit(self, key, *, safety=False):
        async with self._lock:
            now = self._clock()
            bucket = self._buckets[safety]
            # Ordered by last accepted request. Rejected floods never extend TTL.
            while bucket and next(iter(bucket.values())) + self._ttl <= now:
                bucket.popitem(last=False)
            previous = bucket.get(key)
            if previous is not None and now - previous < self._limit:
                metrics.record("safety.rate" if safety else "input.rate")
                return False
            # Do not evict live keys: cycling identities must not reset a limit.
            if previous is None and len(bucket) >= self._capacity:
                metrics.record("safety.capacity" if safety else "input.capacity")
                return False
            bucket[key] = now
            bucket.move_to_end(key)
            metrics.record("safety.accepted" if safety else "input.accepted")
            return True

    def purge(self):
        """Called synchronously on this process's event loop; no await inside."""
        now = self._clock()
        for bucket in self._buckets.values():
            while bucket and next(iter(bucket.values())) + self._ttl <= now:
                bucket.popitem(last=False)

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None) or data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        bot = data.get("bot")
        key = (getattr(bot, "id", None), user.id)
        if await self.admit(key, safety=safety_request(event)):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            try:
                # Stop the spinner, but never execute the dropped operation.
                await event.answer("کمی صبر کن و دوباره بزن.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
                # Expired callbacks / flood waits must not fall through to handler.
                pass
        return None
