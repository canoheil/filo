"""DB access helpers. All writes are small and explicit; keep it boring."""

from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from database.connection import get_session
from database.models import User, BusinessConnection, Persona, AiMessageLog
from services.quota import utc_today


async def get_or_create_user(tg_user_id: int, username: str | None, use_case: str | None = None) -> User:
    async with get_session() as s:
        user = await s.get(User, tg_user_id)
        if user is None:
            user = User(tg_user_id=tg_user_id, username=username, use_case=use_case or "business")
            s.add(user)
            await s.commit()
            await s.refresh(user)
        elif use_case:
            user.use_case = use_case
            await s.commit()
        return user


async def get_or_create_persona(user_id: int) -> Persona:
    async with get_session() as s:
        p = await s.get(Persona, user_id)
        if p is None:
            p = Persona(user_id=user_id)
            s.add(p)
            await s.commit()
            await s.refresh(p)
        return p


async def update_persona(user_id: int, **fields) -> Persona:
    async with get_session() as s:
        p = await s.get(Persona, user_id)
        if p is None:
            p = Persona(user_id=user_id)
            s.add(p)
        for k, v in fields.items():
            setattr(p, k, v)
        p.updated_at = datetime.utcnow()
        await s.commit()
        await s.refresh(p)
        return p


async def upsert_connection(connection_id: str, user_id: int | None, rights: dict | None,
                            tg_chat_id: int | None, is_enabled: bool) -> BusinessConnection:
    async with get_session() as s:
        conn = await s.get(BusinessConnection, connection_id)
        if conn is None:
            conn = BusinessConnection(connection_id=connection_id, user_id=user_id or 0,
                                      rights=rights, tg_chat_id=tg_chat_id, is_enabled=is_enabled)
            s.add(conn)
        else:
            if user_id:
                conn.user_id = user_id
            conn.rights, conn.tg_chat_id, conn.is_enabled = rights, tg_chat_id, is_enabled
            if not is_enabled:
                conn.disconnected_at = datetime.utcnow()
        await s.commit()
        await s.refresh(conn)
        return conn


async def get_connection(connection_id: str) -> BusinessConnection | None:
    async with get_session() as s:
        return await s.get(BusinessConnection, connection_id)


async def log_inbound_once(connection_id: str, chat_id: int, message_id: int) -> bool:
    """Insert the inbound row; False => we've already processed this update (retry)."""
    async with get_session() as s:
        s.add(AiMessageLog(connection_id=connection_id, chat_id=chat_id,
                           message_id=message_id, direction="in", status="ok"))
        try:
            await s.commit()
            return True
        except IntegrityError:
            await s.rollback()
            return False


async def save_draft(connection_id: str, chat_id: int, message_id: int, draft_text: str) -> int:
    async with get_session() as s:
        row = AiMessageLog(connection_id=connection_id, chat_id=chat_id, message_id=message_id,
                           direction="draft", status="draft", draft_text=draft_text)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row.id


async def take_draft(draft_id: int) -> AiMessageLog | None:
    async with get_session() as s:
        row = await s.get(AiMessageLog, draft_id)
        return row


async def finish_draft(draft_id: int, status: str) -> None:
    async with get_session() as s:
        row = await s.get(AiMessageLog, draft_id)
        if row:
            row.status = status
            row.draft_text = None  # purge draft content either way
            await s.commit()


async def log_outbound(connection_id: str, chat_id: int, message_id: int, status: str = "ok") -> None:
    async with get_session() as s:
        s.add(AiMessageLog(connection_id=connection_id, chat_id=chat_id,
                           message_id=message_id, direction="out", status=status))
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()


async def quota_hit_and_count(user_id: int, free_limit: int) -> tuple[bool, int]:
    """Returns (allowed, used_after). Atomically resets on new UTC day."""
    today = utc_today()
    async with get_session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return False, 0
        if user.plan != "free":
            return True, user.quota_used
        if user.quota_date != today:
            user.quota_date, user.quota_used = today, 0
        if user.quota_used >= free_limit:
            await s.commit()
            return False, user.quota_used
        user.quota_used += 1
        await s.commit()
        return True, user.quota_used


async def delete_user_data(tg_user_id: int) -> None:
    async with get_session() as s:
        await s.execute(delete(AiMessageLog).where(
            AiMessageLog.connection_id.in_(select(BusinessConnection.connection_id).where(
                BusinessConnection.user_id == tg_user_id))))
        await s.execute(delete(BusinessConnection).where(BusinessConnection.user_id == tg_user_id))
        await s.execute(delete(Persona).where(Persona.user_id == tg_user_id))
        user = await s.get(User, tg_user_id)
        if user:
            await s.delete(user)
        await s.commit()
