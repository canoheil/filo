"""Bounded process-local AI admission, shared by business and sandbox.

No queue: reserve before quota, release even on cancellation. Not distributed.
"""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from core.config import get_settings


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str = "accepted"


class AIGuard:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = asyncio.Lock()
        self.history = {"chat": OrderedDict(), "owner": OrderedDict()}
        self.notices = OrderedDict()
        self.active = {}
        self.total = 0
        self.last_global = None

    def purge(self, now):
        for bucket in [*self.history.values(), self.notices]:
            while bucket and next(iter(bucket.values())) + 60 <= now:
                bucket.popitem(last=False)

    async def acquire(self, owner, scope):
        cfg = get_settings()
        async with self.lock:
            now = self.clock()
            self.purge(now)
            if self.total >= cfg.AI_MAX_CONCURRENT:
                return Admission(False, "global_busy")
            if self.active.get(owner, 0) >= cfg.AI_OWNER_MAX_CONCURRENT:
                return Admission(False, "owner_busy")
            checks = [("chat", (owner, scope), cfg.AI_CHAT_INTERVAL_SECONDS),
                      ("owner", owner, cfg.AI_OWNER_INTERVAL_SECONDS)]
            for name, key, interval in checks:
                bucket = self.history[name]
                if key in bucket and now - bucket[key] < interval:
                    return Admission(False, name + "_rate")
                if key not in bucket and len(bucket) >= cfg.AI_GUARD_MAX_KEYS:
                    return Admission(False, "capacity")
            if self.last_global is not None and now - self.last_global < cfg.AI_GLOBAL_INTERVAL_SECONDS:
                return Admission(False, "global_rate")
            for name, key, _ in checks:
                self.history[name][key] = now
                self.history[name].move_to_end(key)
            self.last_global = now
            self.active[owner] = self.active.get(owner, 0) + 1
            self.total += 1
            return Admission(True)

    @asynccontextmanager
    async def slot(self, owner, scope):
        result = await self.acquire(owner, scope)
        try:
            yield result
        finally:
            if result.allowed:
                # Synchronous: cancellation cannot interrupt release on this loop.
                self.total -= 1
                self.active[owner] -= 1
                if not self.active[owner]:
                    del self.active[owner]

    def forget(self, owner):
        self.history["owner"].pop(owner, None)
        for key in list(self.history["chat"]):
            if key[0] == owner:
                del self.history["chat"][key]
        for key in list(self.notices):
            if key[0] == owner:
                del self.notices[key]
        # Never release active work here: its context manager owns the lease.

    async def notice(self, owner, kind):
        async with self.lock:
            now = self.clock()
            self.purge(now)
            key = (owner, kind)
            if key in self.notices or len(self.notices) >= get_settings().AI_GUARD_MAX_KEYS:
                return False
            self.notices[key] = now
            return True


ai_guard = AIGuard()
