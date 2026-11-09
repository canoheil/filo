"""Durable per-chat routing. All lookups/actions are owner scoped."""
from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from database.connection import get_session
from database.models import ChatControl, BusinessConnection, AiMessageLog


async def ensure(owner, connection, chat):
    async with get_session() as s:
        conn = await s.get(BusinessConnection, connection)
        if conn is None or conn.user_id != owner:
            return None
        criteria = (ChatControl.user_id == owner, ChatControl.connection_id == connection, ChatControl.chat_id == chat)
        row = (await s.execute(select(ChatControl).where(*criteria))).scalar_one_or_none()
        if row is None:
            try:
                async with s.begin_nested():
                    row = ChatControl(user_id=owner, connection_id=connection, chat_id=chat)
                    s.add(row)
                    await s.flush()
            except IntegrityError:
                row = (await s.execute(select(ChatControl).where(*criteria).with_for_update())).scalar_one()
        await s.commit()
        await s.refresh(row)
        return row


def reason(row, now=None):
    if row is None:
        return "unavailable"
    if row.is_paused:
        return "chat_paused"
    if row.handoff_until and row.handoff_until > (now or datetime.utcnow()):
        return "owner_takeover"
    return "active"


async def check(owner, connection, chat):
    return reason(await ensure(owner, connection, chat))


async def note(owner, connection, chat, value):
    if value not in {"active", "chat_paused", "owner_takeover", "global_paused", "quota", "unavailable", "reply_superseded"}:
        raise ValueError("Invalid reason")
    async with get_session() as s:
        await s.execute(update(ChatControl).where(
            ChatControl.user_id == owner, ChatControl.connection_id == connection, ChatControl.chat_id == chat
        ).values(last_reason=value, updated_at=datetime.utcnow()))
        await s.commit()


async def owner_replied(owner, connection, chat, message_id, minutes):
    if minutes not in {15, 30, 60}:
        return
    row = await ensure(owner, connection, chat)
    if row is None:
        return
    async with get_session() as s:
        changed = await s.execute(update(ChatControl).where(
            ChatControl.id == row.id, ChatControl.user_id == owner,
            ChatControl.owner_message_id < message_id
        ).values(handoff_until=datetime.utcnow()+timedelta(minutes=minutes),
                 owner_message_id=message_id, routing_revision=ChatControl.routing_revision+1, last_reason="owner_takeover", updated_at=datetime.utcnow()))
        if changed.rowcount == 1:
            await s.execute(update(AiMessageLog).where(
                AiMessageLog.connection_id == connection, AiMessageLog.chat_id == chat,
                AiMessageLog.direction == "draft", AiMessageLog.status == "draft"
            ).values(status="blocked"))
        await s.commit()


async def list_chats(owner, page=0):
    async with get_session() as s:
        result = await s.execute(select(ChatControl).where(ChatControl.user_id == owner)
                                .order_by(ChatControl.id.desc()).offset(page*5).limit(6))
        return list(result.scalars())


async def get_chat(owner, id_):
    async with get_session() as s:
        result = await s.execute(select(ChatControl).where(ChatControl.user_id == owner, ChatControl.id == id_))
        return result.scalar_one_or_none()


async def set_chat(owner, id_, paused):
    async with get_session() as s:
        result = await s.execute(update(ChatControl).where(ChatControl.user_id == owner, ChatControl.id == id_)
            .values(is_paused=paused, handoff_until=None, routing_revision=ChatControl.routing_revision+1, last_reason="chat_paused" if paused else "active",
                    updated_at=datetime.utcnow()))
        await s.commit()
        return result.rowcount == 1


async def version(owner, connection, chat):
    row = await ensure(owner, connection, chat)
    return row.routing_revision if row else None
