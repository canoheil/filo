"""Deadline checks apply even when the maintenance task has not run."""
from datetime import datetime, timedelta
from sqlalchemy import select, update
from core.config import get_settings
from database.connection import get_session
from database.models import AiMessageLog, BusinessConnection


def cutoff(now=None):
    return (now or datetime.utcnow()) - timedelta(minutes=get_settings().DRAFT_TTL_MINUTES)


def expired(row):
    created = getattr(row, "created_at", None)
    return created is not None and created <= cutoff()


def caption(row):
    created = getattr(row, "created_at", None)
    if created is None:
        return ""
    deadline = created + timedelta(minutes=get_settings().DRAFT_TTL_MINUTES)
    return f"\nایجاد: {created:%Y-%m-%d %H:%M} UTC | مهلت ارسال: {deadline:%Y-%m-%d %H:%M} UTC"


async def maintenance(now=None):
    now = now or datetime.utcnow()
    retention = now - timedelta(hours=get_settings().DRAFT_ERROR_RETENTION_HOURS)
    async with get_session() as s:
        await s.execute(update(AiMessageLog).where(
            AiMessageLog.direction == "draft", AiMessageLog.status == "draft",
            AiMessageLog.created_at <= cutoff(now)
        ).values(status="expired", draft_text=None))
        # Old abandoned claims remain ambiguous, never returned to a sendable state.
        await s.execute(update(AiMessageLog).where(
            AiMessageLog.direction == "draft", AiMessageLog.status == "sending",
            AiMessageLog.created_at <= retention
        ).values(status="uncertain", draft_text=None))
        await s.execute(update(AiMessageLog).where(
            AiMessageLog.direction == "draft", AiMessageLog.created_at <= retention,
            AiMessageLog.draft_text.is_not(None), AiMessageLog.status != "sending"
        ).values(draft_text=None))
        await s.execute(update(AiMessageLog).where(
            AiMessageLog.direction == "draft", AiMessageLog.status == "expired",
            AiMessageLog.created_at <= retention
        ).values(status="closed"))
        await s.commit()


async def expire_claim(draft_id):
    async with get_session() as s:
        await s.execute(update(AiMessageLog).where(
            AiMessageLog.id == draft_id, AiMessageLog.status == "sending"
        ).values(status="expired", draft_text=None))
        await s.commit()


async def close_failed(owner_id, draft_id):
    async with get_session() as s:
        owned = select(BusinessConnection.connection_id).where(BusinessConnection.user_id == owner_id)
        result = await s.execute(update(AiMessageLog).where(
            AiMessageLog.id == draft_id, AiMessageLog.connection_id.in_(owned),
            AiMessageLog.direction == "draft", AiMessageLog.status.in_(["blocked", "uncertain", "expired"])
        ).values(status="closed", draft_text=None))
        await s.commit()
        return result.rowcount == 1


async def reject_pending(owner_id, before):
    async with get_session() as s:
        owned = select(BusinessConnection.connection_id).where(BusinessConnection.user_id == owner_id)
        result = await s.execute(update(AiMessageLog).where(
            AiMessageLog.connection_id.in_(owned), AiMessageLog.direction == "draft",
            AiMessageLog.status == "draft", AiMessageLog.created_at <= before
        ).values(status="rejected", draft_text=None))
        await s.commit()
        return result.rowcount
