"""Simple per-user throttle (adapted from sib). Protects both Telegram
rate limits and our shared free AI Studio quota."""

import asyncio
import time

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, limit_seconds: float = 0.8) -> None:
        self._lock = asyncio.Lock()
        self._last: dict[int, float] = {}
        self._limit = limit_seconds

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        now = time.monotonic()
        async with self._lock:
            last = self._last.get(user.id, 0.0)
            if now - last < self._limit:
                return None  # silently drop bursts; handlers stay cheap
            self._last[user.id] = now
        return await handler(event, data)
