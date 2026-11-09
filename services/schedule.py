"""Local wall-clock weekly schedule. No dependence on the server's timezone."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from database.connection import get_session
from database.models import ReplySchedule


def validate(zone, days, start, end):
    if not isinstance(zone, str) or not zone or len(zone) > 64:
        raise ValueError("Choose a timezone")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Invalid timezone") from None
    if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError("Choose weekdays")
    if type(start) is not int or type(end) is not int or not 0 <= start < 1440 or not 0 <= end <= 1440 or start == end:
        raise ValueError("Invalid time range")


def allowed(row, now=None):
    if row is None or not row.enabled:
        return True
    try:
        validate(row.timezone, row.days, row.start_minute, row.end_minute)
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("Use an aware instant")
        local = instant.astimezone(ZoneInfo(row.timezone))
        day, minute = local.weekday(), local.hour*60 + local.minute
        if row.start_minute < row.end_minute:
            return day in row.days and row.start_minute <= minute < row.end_minute
        # Selected weekdays are the START days for overnight intervals.
        return ((day in row.days and minute >= row.start_minute) or
                ((day - 1) % 7 in row.days and minute < row.end_minute))
    except (ValueError, TypeError, AttributeError):
        return False  # fail closed on corrupt configuration


async def read(owner):
    async with get_session() as s:
        return await s.get(ReplySchedule, owner)


async def check(owner):
    return allowed(await read(owner))


async def version(owner):
    row = await read(owner)
    return row.revision if row else 0
