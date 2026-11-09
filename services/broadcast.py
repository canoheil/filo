"""Single-process durable broadcast outbox. Ambiguous deliveries are never retried."""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, update, delete, insert, literal, func, or_
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from database.connection import get_session
from database.models import BroadcastJob as Job, BroadcastTarget as Target, BroadcastSubscriber as Subscriber, AdminAudit
from services.admin import authorized

SUPPORTED = {"text", "photo", "video", "document", "animation", "audio", "voice", "video_note", "sticker"}


def supported(message):
    return not message.media_group_id and not message.has_protected_content and message.content_type in SUPPORTED


async def subscribe(user_id):
    from services.store import _ensure_row
    async with get_session() as s:
        row = await _ensure_row(s, Subscriber, user_id, user_id=user_id)
        row.enabled = True
        await s.commit()


def require_admin(actor):
    if not authorized(actor):
        raise PermissionError()


async def create(actor, preview_message_id):
    require_admin(actor)
    async with get_session() as s:
        job = Job(admin_id=actor, source_chat_id=actor, source_message_id=preview_message_id)
        s.add(job)
        await s.flush()
        await s.execute(insert(Target).from_select(
            ["job_id", "user_id", "state", "attempts"],
            select(literal(job.id), Subscriber.user_id, literal("pending"), literal(0)).where(Subscriber.enabled.is_(True))))
        s.add(AdminAudit(admin_id=actor, action="broadcast_create", target_id=job.id))
        await s.commit()
        return job.id


async def control(actor, job_id, action):
    require_admin(actor)
    async with get_session() as s:
        job = await s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            return False
        if action == "confirm":
            if job.admin_id != actor or job.state != "draft" or job.created_at <= datetime.utcnow() - timedelta(days=1):
                return False
            job.state = "queued"
        elif action == "stop":
            if job.state not in {"draft", "queued"}:
                return False
            job.state = "stopped"
            await s.execute(update(Target).where(Target.job_id == job_id, Target.state == "pending").values(state="skipped"))
        else:
            raise ValueError("Invalid broadcast action")
        s.add(AdminAudit(admin_id=actor, action="broadcast_" + action, target_id=job.id))
        await s.commit()
        return True


async def report(actor, job_id):
    require_admin(actor)
    async with get_session() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return None
        counts = dict((await s.execute(select(Target.state, func.count()).where(Target.job_id == job_id).group_by(Target.state))).all())
        return job, counts


async def recent(actor):
    require_admin(actor)
    async with get_session() as s:
        return (await s.scalars(select(Job).order_by(Job.id.desc()).limit(10))).all()


async def recover():
    """Only at startup, with no other worker/polling process alive."""
    async with get_session() as s:
        await s.execute(update(Target).where(Target.state == "sending").values(state="unknown"))
        await s.commit()


async def maintenance():
    now = datetime.utcnow()
    async with get_session() as s:
        ids = select(Job.id).where(Job.state == "draft", Job.created_at < now - timedelta(days=1))
        await s.execute(update(Target).where(Target.job_id.in_(ids), Target.state == "pending").values(state="skipped"))
        await s.execute(update(Job).where(Job.state == "draft", Job.created_at < now - timedelta(days=1)).values(state="expired"))
        expired_report = (Job.state.in_(["completed", "stopped", "expired"]),
                          Job.created_at < now - timedelta(days=30),
                          or_(Job.retry_at.is_(None), Job.retry_at <= now))
        old = select(Job.id).where(*expired_report)
        await s.execute(delete(Target).where(Target.job_id.in_(old)))
        await s.execute(delete(Job).where(*expired_report))
        await s.commit()


async def claim():
    async with get_session() as s:
        # Serialize with stop/confirm. Only one worker is supported.
        job = await s.scalar(select(Job).where(Job.state == "queued").order_by(Job.id).limit(1).with_for_update())
        if job is None:
            return None
        if not authorized(job.admin_id):
            job.state = "stopped"
            await s.execute(update(Target).where(Target.job_id == job.id, Target.state == "pending").values(state="skipped"))
            await s.commit()
            return None
        # Telegram flood waits apply across campaigns, even if an admin stops
        # the campaign which encountered the limit and confirms another one.
        cooldown = await s.scalar(select(func.max(Job.retry_at)))
        if cooldown and cooldown > datetime.utcnow():
            return None
        target = await s.scalar(select(Target).where(Target.job_id == job.id, Target.state == "pending").order_by(Target.user_id).limit(1).with_for_update())
        if target is None:
            # No request is in flight in this single worker. A previous DB
            # finalization failure must not masquerade as a known delivery.
            await s.execute(update(Target).where(Target.job_id == job.id, Target.state == "sending").values(state="unknown"))
            job.state = "completed"
            await s.commit()
            return None
        subscriber = await s.get(Subscriber, target.user_id)
        if subscriber is None or not subscriber.enabled:
            target.state = "skipped"
            await s.commit()
            return None
        target.state = "sending"
        target.attempts += 1
        await s.commit()
        return job.id, target.user_id, job.source_chat_id, job.source_message_id


async def finish(job_id, user_id, outcome, retry_after=None):
    async with get_session() as s:
        job = await s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        target = await s.scalar(select(Target).where(Target.job_id == job_id, Target.user_id == user_id).with_for_update())
        if job is None or target is None or target.state != "sending":
            return
        if outcome == "retry":
            target.state = "pending" if job.state == "queued" and target.attempts < 5 else "skipped" if job.state != "queued" else "failed"
            job.retry_at = datetime.utcnow() + timedelta(seconds=max(1, retry_after))
        else:
            target.state = outcome
        if outcome == "blocked":
            subscriber = await s.get(Subscriber, user_id)
            if subscriber:
                subscriber.enabled = False
        await s.commit()


async def step(bot):
    item = await claim()
    if item is None:
        return False
    job_id, user_id, source_chat, source_message = item
    try:
        # Preview is bot-owned and immutable to edits of the original admin message.
        await bot.copy_message(chat_id=user_id, from_chat_id=source_chat, message_id=source_message,
                               request_timeout=20)
    except TelegramRetryAfter as exc:
        await finish(job_id, user_id, "retry", exc.retry_after)
    except TelegramForbiddenError:
        await finish(job_id, user_id, "blocked")
    except TelegramBadRequest:
        await finish(job_id, user_id, "failed")
    except Exception as exc:
        logging.getLogger("secretary").warning("broadcast uncertain type=%s", type(exc).__name__)
        await finish(job_id, user_id, "unknown")
    else:
        await finish(job_id, user_id, "sent")
    return True


async def worker(bot):
    ticks = 0
    while True:
        try:
            if ticks % 120 == 0:
                await maintenance()
            ticks += 1
            worked = await step(bot)
        except Exception as exc:
            # A failed finalization remains sending; never re-claim within process.
            logging.getLogger("secretary").warning("broadcast worker failed type=%s", type(exc).__name__)
            try:
                # step has unwound, so this worker has no in-flight API call.
                await recover()
            except Exception as recovery_error:
                logging.getLogger("secretary").warning("broadcast recovery failed type=%s", type(recovery_error).__name__)
            worked = False
        await asyncio.sleep(0.1 if worked else 1)
