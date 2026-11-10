"""Persistent membership policy. Serialize writes on the singleton policy row."""
from sqlalchemy import select, func
from database.connection import get_session
from database.models import MembershipPolicy, RequiredChannelRow, AdminAudit, User, BusinessConnection
from core.config import get_settings
from core.membership import required_channels, ChannelPolicy


def authorized(user_id):
    return user_id in get_settings().admin_ids


async def initialize():
    # Startup only, before polling. A committed marker prevents env resurrection.
    async with get_session() as s:
        if await s.get(MembershipPolicy, 1) is None:
            s.add(MembershipPolicy(id=1, revision=0))
            for channel in get_settings().required_channels:
                s.add(RequiredChannelRow(chat_id=str(channel.chat_id), join_url=channel.join_url))
            await s.commit()


async def snapshot():
    async with get_session() as s:
        # Consistent policy + channel snapshot, including during concurrent edits.
        policy = await s.scalar(select(MembershipPolicy).where(MembershipPolicy.id == 1).with_for_update())
        if policy is None:
            raise RuntimeError("Membership policy not initialized")
        rows = (await s.scalars(select(RequiredChannelRow).where(RequiredChannelRow.enabled.is_(True)).order_by(RequiredChannelRow.id))).all()
        return ChannelPolicy(policy.revision, tuple(required_channels(r.chat_id, r.join_url)[0] for r in rows))


async def channels():
    return (await snapshot()).channels


async def listing():
    async with get_session() as s:
        return (await s.scalars(select(RequiredChannelRow).order_by(RequiredChannelRow.id))).all()


async def stats():
    async with get_session() as s:
        return (await s.scalar(select(func.count()).select_from(User)),
                await s.scalar(select(func.count()).select_from(BusinessConnection).where(BusinessConnection.is_enabled.is_(True))))


async def mutate(actor, action, *, channel=None, row_id=None, version=None):
    if not authorized(actor):
        raise PermissionError()
    async with get_session() as s:
        policy = await s.scalar(select(MembershipPolicy).where(MembershipPolicy.id == 1).with_for_update())
        if policy is None:
            raise RuntimeError("Membership policy not initialized")
        if action == "add":
            if await s.scalar(select(func.count()).select_from(RequiredChannelRow)) >= 10:
                return False
            existing = await s.scalar(select(RequiredChannelRow).where(RequiredChannelRow.chat_id == str(channel.chat_id)))
            if existing:
                return False
            row = RequiredChannelRow(chat_id=str(channel.chat_id), join_url=channel.join_url)
            s.add(row)
            await s.flush()
        elif action in {"enable", "disable", "delete"}:
            row = await s.get(RequiredChannelRow, row_id)
            if row is None or row.version != version:
                return False
            if action == "delete":
                await s.delete(row)
            else:
                row.enabled = action == "enable"
                row.version += 1
        else:
            raise ValueError("Unknown action")
        policy.revision += 1
        s.add(AdminAudit(admin_id=actor, action=action, target_id=row.id))
        await s.commit()
    return True


async def recharge(actor: int, user_id: int) -> bool:
    """Reset an existing user's quota under the same parent lock as reservations."""
    if not authorized(actor):
        raise PermissionError()
    if not isinstance(user_id, int) or isinstance(user_id, bool) or not 0 < user_id < 2**63:
        raise ValueError("Invalid Telegram user ID")
    async with get_session() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == user_id).with_for_update())
        if user is None:
            return False
        user.quota_used = 0
        # Audit target_id is an internal Integer, not a Telegram BigInteger.
        # Record the admin action without truncating a potentially large TG ID.
        s.add(AdminAudit(admin_id=actor, action="quota_recharge", target_id=None))
        await s.commit()
        return True
