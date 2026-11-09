"""Owner-scoped statistics and safe, monotonic AI outcomes."""
from datetime import datetime, timedelta
import hashlib
import logging
from sqlalchemy import select, func, update, case
from database.connection import get_session
from database.models import AiRequest, AiMessageLog, BusinessConnection
from services.draft_lifecycle import cutoff

SAFE_ERRORS = {"transport", "invalid_response", "http_400", "http_401", "http_403", "http_404",
               "http_429", "http_500", "http_502", "http_503", "http_504"}


def business_key(owner, connection, chat, message):
    return hashlib.sha256(f"business:{owner}:{connection}:{chat}:{message}".encode()).hexdigest()


def sandbox_key(owner, chat, message):
    return hashlib.sha256(f"sandbox:{owner}:{chat}:{message}".encode()).hexdigest()


def safe_error(exc):
    from services.ai import AIError
    return str(exc) if isinstance(exc, AIError) and str(exc) in SAFE_ERRORS else "internal"


async def finish(owner, key, error=None):
    """Best-effort diagnostics must not trigger a second AI call or delivery."""
    try:
        async with get_session() as s:
            await s.execute(update(AiRequest).where(
                AiRequest.request_key == key, AiRequest.user_id == owner, AiRequest.state == "reserved"
            ).values(state="failed" if error is not None else "succeeded",
                     error_code=safe_error(error) if error is not None else None))
            await s.commit()
    except Exception as exc:
        logging.getLogger("secretary").warning("AI outcome write failed type=%s", type(exc).__name__)


async def report(owner, now=None):
    now = now or datetime.utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    async with get_session() as s:
        requests = (await s.execute(select(AiRequest.source, AiRequest.state, func.count()).where(
            AiRequest.user_id == owner, AiRequest.created_at >= start, AiRequest.created_at < end
        ).group_by(AiRequest.source, AiRequest.state))).all()
        connections = select(BusinessConnection.connection_id).where(BusinessConnection.user_id == owner)
        base = (AiMessageLog.connection_id.in_(connections),)
        sent = (await s.execute(select(func.count()).select_from(AiMessageLog).where(
            *base, AiMessageLog.direction == "out", AiMessageLog.status == "ok",
            AiMessageLog.created_at >= start, AiMessageLog.created_at < end))).scalar_one()
        effective = case(( (AiMessageLog.status == "draft") & (AiMessageLog.created_at <= cutoff(now)), "expired"),
                         else_=AiMessageLog.status)
        drafts = (await s.execute(select(effective, func.count()).where(
            *base, AiMessageLog.direction == "draft", AiMessageLog.created_at >= start, AiMessageLog.created_at < end
        ).group_by(effective))).all()
        open_drafts = (await s.execute(select(effective, func.count()).where(
            *base, AiMessageLog.direction == "draft",
            AiMessageLog.status.in_(["draft", "sending", "blocked", "uncertain", "expired"])
        ).group_by(effective))).all()
        inbound = (await s.execute(select(AiMessageLog.status, func.count()).where(
            *base, AiMessageLog.direction == "in", AiMessageLog.created_at >= start, AiMessageLog.created_at < end
        ).group_by(AiMessageLog.status))).all()
        errors = (await s.execute(select(AiRequest.error_code, func.count()).where(
            AiRequest.user_id == owner, AiRequest.state == "failed", AiRequest.created_at >= start,
            AiRequest.created_at < end).group_by(AiRequest.error_code))).all()
    return {"date": start.date().isoformat(), "requests": requests, "sent": sent,
            "drafts": dict(drafts), "open": dict(open_drafts), "inbound": dict(inbound), "errors": dict(errors)}


async def inbound(connection, chat, message, status):
    try:
        from services.store import mark_inbound_outcome
        await mark_inbound_outcome(connection, chat, message, status)
    except Exception as exc:
        logging.getLogger("secretary").warning("inbound outcome write failed type=%s", type(exc).__name__)
