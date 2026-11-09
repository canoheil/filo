"""DB access helpers. All writes are small and explicit; keep it boring."""

from datetime import datetime

from sqlalchemy import select, delete, update, case, or_
from sqlalchemy.exc import IntegrityError

from database.connection import get_session
from database.models import User, BusinessConnection, Persona, AiMessageLog, SetupProgress
from services.quota import utc_today


async def _ensure_row(session, model, key: int, **values):
    """Create under a savepoint; a competing insert must not abort the caller."""
    row = await session.get(model, key)
    if row is None:
        try:
            async with session.begin_nested():
                row = model(**values)
                session.add(row)
                await session.flush()
        except IntegrityError:
            # A locking read sees the winner even under MariaDB REPEATABLE READ.
            pk = model.__mapper__.primary_key[0]
            row = (await session.execute(select(model).where(pk == key).with_for_update()
                                         .execution_options(populate_existing=True))).scalar_one_or_none()
            if row is None:
                raise
    return row


async def get_or_create_user(tg_user_id: int, username: str | None, use_case: str | None = None) -> User:
    async with get_session() as s:
        user = await _ensure_row(s, User, tg_user_id, tg_user_id=tg_user_id,
                                 username=username, use_case=use_case or "business")
        if username is not None:
            user.username = username
        if use_case is not None:
            user.use_case = use_case
        await s.commit()
        await s.refresh(user)
        return user


async def get_or_create_persona(user_id: int) -> Persona:
    async with get_session() as s:
        await _ensure_row(s, User, user_id, tg_user_id=user_id)
        p = await _ensure_row(s, Persona, user_id, user_id=user_id)
        await s.commit()
        await s.refresh(p)
        return p


async def update_persona(user_id: int, **fields) -> Persona:
    allowed = {"name", "role", "tone", "language", "knowledge_base", "instructions",
               "signature", "auto_reply", "is_paused", "context_enabled"}
    if fields.keys() - allowed:
        raise ValueError("Unsupported persona field")
    async with get_session() as s:
        await _ensure_row(s, User, user_id, tg_user_id=user_id)
        p = await _ensure_row(s, Persona, user_id, user_id=user_id)
        await s.execute(update(Persona).where(Persona.user_id == user_id).values(
            **fields, updated_at=datetime.utcnow()))
        await s.commit()
        await s.refresh(p)
        # Old-role replies should not leak into a newly edited persona's context.
        from services.context import recent_context
        recent_context.clear_owner(user_id)
        return p


async def upsert_connection(connection_id: str, user_id: int | None, rights: dict | None,
                            tg_chat_id: int | None, is_enabled: bool) -> BusinessConnection:
    async with get_session() as s:
        if user_id is not None:
            await _ensure_row(s, User, user_id, tg_user_id=user_id)
        conn = await s.get(BusinessConnection, connection_id)
        if conn is None:
            if user_id is None:
                raise ValueError("A new connection requires an owner")
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


async def claim_draft(draft_id: int, owner_id: int, *, reject: bool = False) -> AiMessageLog | None:
    """Atomically authorize and consume a pending draft before any network I/O.

    A sending/uncertain draft is never automatically retried: Telegram sends
    cannot participate in our transaction and a timeout may hide a successful send.
    """
    async with get_session() as s:
        owned_connections = select(BusinessConnection.connection_id).where(
            BusinessConnection.user_id == owner_id)
        result = await s.execute(
            update(AiMessageLog).where(
                AiMessageLog.id == draft_id,
                AiMessageLog.direction == "draft",
                AiMessageLog.status == "draft",
                AiMessageLog.connection_id.in_(owned_connections),
            ).values(status="rejected" if reject else "sending",
                     **({"draft_text": None} if reject else {}))
        )
        if result.rowcount != 1:
            await s.rollback()
            return None
        await s.commit()
        return await s.get(AiMessageLog, draft_id)


async def set_draft_delivery_state(draft_id: int, status: str) -> None:
    """Keep content on blocked/uncertain delivery for manual investigation."""
    if status not in {"blocked", "uncertain"}:
        raise ValueError("Unsupported delivery state")
    async with get_session() as s:
        await s.execute(update(AiMessageLog).where(
            AiMessageLog.id == draft_id, AiMessageLog.status == "sending"
        ).values(status=status))
        await s.commit()


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
    """Reserve one AI request with a conditional UPDATE (including UTC rollover).

    Keep the write lock through reading the result. No external calls occur in
    this transaction. Failed AI calls still consume the reservation by design.
    """
    today = utc_today()
    async with get_session() as s:
        result = await s.execute(update(User).where(
            User.tg_user_id == user_id,
            User.plan == "free",
            free_limit > 0,
            or_(User.quota_date != today, User.quota_used < free_limit),
        ).ordered_values(
            # MySQL evaluates SET left-to-right: read the OLD date before changing it.
            (User.quota_used, case((User.quota_date != today, 1), else_=User.quota_used + 1)),
            (User.quota_date, today),
        ))
        user = await s.get(User, user_id)
        allowed = user is not None and (user.plan != "free" or result.rowcount == 1)
        used = user.quota_used if user is not None else 0
        await s.commit()
        return allowed, used


async def delete_user_data(tg_user_id: int) -> None:
    from services.context import recent_context
    recent_context.clear_owner(tg_user_id)
    async with get_session() as s:
        await s.execute(delete(AiMessageLog).where(
            AiMessageLog.connection_id.in_(select(BusinessConnection.connection_id).where(
                BusinessConnection.user_id == tg_user_id))))
        await s.execute(delete(BusinessConnection).where(BusinessConnection.user_id == tg_user_id))
        await s.execute(delete(SetupProgress).where(SetupProgress.user_id == tg_user_id))
        await s.execute(delete(Persona).where(Persona.user_id == tg_user_id))
        user = await s.get(User, tg_user_id)
        if user:
            await s.delete(user)
        await s.commit()


async def list_open_drafts(owner_id: int) -> list[AiMessageLog]:
    """Owner-scoped recovery view; ambiguous deliveries have no resend action."""
    async with get_session() as s:
        result = await s.execute(select(AiMessageLog).join(
            BusinessConnection, BusinessConnection.connection_id == AiMessageLog.connection_id
        ).where(
            BusinessConnection.user_id == owner_id,
            AiMessageLog.direction == "draft",
            AiMessageLog.status.in_(["draft", "sending", "blocked", "uncertain"]),
        ).order_by(AiMessageLog.id.desc()).limit(20))
        return list(result.scalars())


async def list_user_connections(owner_id: int) -> list[BusinessConnection]:
    async with get_session() as s:
        result = await s.execute(select(BusinessConnection).where(
            BusinessConnection.user_id == owner_id))
        return list(result.scalars())


async def reset_damaged_persona_field(owner_id: int, field: str) -> bool:
    """Explicit user-approved reset; preserve healthy or concurrently edited values."""
    from services.persona_health import DEFAULTS, is_question_only
    if field not in DEFAULTS:
        raise ValueError("Unsupported repair field")
    async with get_session() as s:
        p = await s.get(Persona, owner_id)
        if p is None or not is_question_only(getattr(p, field)):
            return False
        column = getattr(Persona, field)
        result = await s.execute(update(Persona).where(
            Persona.user_id == owner_id, column == getattr(p, field)
        ).values(**{field: DEFAULTS[field]}, updated_at=datetime.utcnow()))
        await s.commit()
        return result.rowcount == 1


async def get_setup_progress(owner_id: int) -> SetupProgress:
    async with get_session() as s:
        await _ensure_row(s, User, owner_id, tg_user_id=owner_id)
        progress = await _ensure_row(s, SetupProgress, owner_id, user_id=owner_id)
        await s.commit()
        await s.refresh(progress)
        return progress


async def mark_setup_step(owner_id: int, step: str) -> None:
    if step not in {"use_case", "name", "role", "tone", "knowledge_base", "instructions"}:
        raise ValueError("Unsupported setup step")
    await get_setup_progress(owner_id)
    async with get_session() as s:
        progress = (await s.execute(select(SetupProgress).where(
            SetupProgress.user_id == owner_id).with_for_update())).scalar_one()
        progress.completed = list(dict.fromkeys([*progress.completed, step]))
        await s.commit()


async def record_setup_test(owner_id: int, fingerprint: str) -> None:
    await get_setup_progress(owner_id)
    async with get_session() as s:
        await s.execute(update(SetupProgress).where(SetupProgress.user_id == owner_id).values(
            tested_hash=fingerprint, approved_hash=None))
        await s.commit()


async def approve_setup_test(owner_id: int, fingerprint: str) -> bool:
    async with get_session() as s:
        result = await s.execute(update(SetupProgress).where(
            SetupProgress.user_id == owner_id, SetupProgress.tested_hash == fingerprint
        ).values(approved_hash=fingerprint))
        await s.commit()
        return result.rowcount == 1
