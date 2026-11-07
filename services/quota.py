"""Pure quota helpers (unit-testable, no DB)."""

from datetime import datetime, timezone


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def idem_key(connection_id: str, chat_id: int, message_id: int, direction: str) -> str:
    """Natural idempotency key for one logical event (Telegram may retry)."""
    return f"ai_reply:{connection_id}:{chat_id}:{message_id}:{direction}"


def quota_exhausted(quota_date: str, quota_used: int, today: str, limit: int) -> bool:
    """True if user is over the free limit for today (UTC day rollover)."""
    if quota_date != today:
        return False  # fresh day — counter will be reset by the caller
    return quota_used >= limit
