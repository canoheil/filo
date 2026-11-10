from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.base import Base
from database.models import AdminAudit
from core.config import Settings
from core.membership import required_channels
from services import admin
from services.membership import MembershipService
from handlers.admin import AdminGuard, verify
from aiogram.types import Message, Chat, User


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/admin.db')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def session():
        async with factory() as s:
            yield s
    monkeypatch.setattr(admin, 'get_session', session)
    monkeypatch.setattr(admin, 'get_settings', lambda: Settings(_env_file=None, ADMIN_IDS='42', SUPPORT_CHANNEL='@filo_support'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_delete_does_not_resurrect_and_audits(db):
    await admin.initialize()
    await admin.initialize()
    rows = await admin.listing()
    assert len(rows) == 1
    assert await admin.mutate(42, 'delete', row_id=rows[0].id, version=1)
    await admin.initialize()
    assert await admin.channels() == ()
    async with db() as s:
        audit = (await s.scalars(select(AdminAudit))).one()
        assert audit.admin_id == 42 and audit.action == 'delete'


@pytest.mark.asyncio
async def test_authorization_versions_duplicate_and_enable_disable(db):
    await admin.initialize()
    channel = required_channels('@sponsor_channel')[0]
    with pytest.raises(PermissionError):
        await admin.mutate(7, 'add', channel=channel)
    assert await admin.mutate(42, 'add', channel=channel)
    assert not await admin.mutate(42, 'add', channel=channel)
    row = (await admin.listing())[-1]
    assert await admin.mutate(42, 'disable', row_id=row.id, version=1)
    assert not await admin.mutate(42, 'delete', row_id=row.id, version=1)
    assert len(await admin.channels()) == 1
    assert await admin.mutate(42, 'enable', row_id=row.id, version=2)
    assert len(await admin.channels()) == 2


@pytest.mark.asyncio
async def test_dynamic_policy_checks_all_active_channels_and_invalidates_cache(db):
    await admin.initialize()
    service = MembershipService()
    service.channel_loader = admin.channels
    bot = SimpleNamespace(id=99, get_chat_member=AsyncMock(return_value=SimpleNamespace(status='administrator')))
    assert (await service.check(bot, 5)).state == 'member'
    assert bot.get_chat_member.await_count == 2
    await admin.mutate(42, 'add', channel=required_channels('@sponsor_channel')[0])
    bot.get_chat_member.side_effect = [SimpleNamespace(status='administrator'), SimpleNamespace(status='member'),
                                     SimpleNamespace(status='administrator'), SimpleNamespace(status='left')]
    assert (await service.check(bot, 5)).state == 'missing'
    assert bot.get_chat_member.await_count == 6


@pytest.mark.asyncio
async def test_policy_failure_is_closed_and_safe():
    service = MembershipService()
    service.channel_loader = AsyncMock(side_effect=RuntimeError('SECRET'))
    result = await service.check(SimpleNamespace(id=99), 1)
    assert result.state == 'unavailable'


@pytest.mark.asyncio
@pytest.mark.parametrize('uid,chat_type,chat_id,allowed', [(42,'private',42,True),(7,'private',7,False),(42,'supergroup',-1001,False)])
async def test_admin_guard_every_operation(monkeypatch, uid, chat_type, chat_id, allowed):
    monkeypatch.setattr(admin, 'authorized', lambda u: u == 42)
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=chat_id,type=chat_type), from_user=User(id=uid,is_bot=False,first_name='a'))
    monkeypatch.setattr(Message, 'answer', AsyncMock())
    handler = AsyncMock()
    await AdminGuard()(handler, message, {})
    assert handler.await_count == int(allowed)


@pytest.mark.asyncio
async def test_verification_rejects_groups_and_nonadmin():
    bot = SimpleNamespace(id=99, get_chat=AsyncMock(return_value=SimpleNamespace(id=-100123, type='supergroup')),
                          get_chat_member=AsyncMock(return_value=SimpleNamespace(status='administrator')))
    channel = required_channels('@sponsor_channel')[0]
    assert not await verify(bot, channel)
    bot.get_chat.return_value.type = 'channel'
    bot.get_chat_member.return_value.status = 'member'
    assert not await verify(bot, channel)
    bot.get_chat_member.return_value.status = 'administrator'
    assert await verify(bot, channel)


@pytest.mark.asyncio
async def test_revision_invalidates_even_disable_then_reenable(db):
    await admin.initialize()
    service = MembershipService()
    service.channel_loader = admin.snapshot
    bot = SimpleNamespace(id=99, get_chat_member=AsyncMock(return_value=SimpleNamespace(status='administrator')))
    await service.check(bot, 5)
    row = (await admin.listing())[0]
    await admin.mutate(42, 'disable', row_id=row.id, version=1)
    await admin.mutate(42, 'enable', row_id=row.id, version=2)
    bot.get_chat_member.side_effect = [SimpleNamespace(status='administrator'), SimpleNamespace(status='left')]
    assert (await service.check(bot, 5)).state == 'missing'


def test_admin_ids_validation():
    from pydantic import ValidationError
    assert Settings(_env_file=None, ADMIN_IDS='42, 123').admin_ids == {42, 123}
    for value in ['42,', '0', '-1', 'me', '۴۲']:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, ADMIN_IDS=value)


@pytest.mark.asyncio
async def test_admin_entry_and_state_bypass_but_product_does_not(monkeypatch):
    from middleware.membership import MembershipMiddleware
    from services.membership import membership, MembershipResult
    monkeypatch.setattr(admin, 'authorized', lambda u: u == 42)
    check = AsyncMock(return_value=MembershipResult('missing'))
    monkeypatch.setattr(membership, 'check', check)
    monkeypatch.setattr(membership, 'channel_loader', AsyncMock())
    monkeypatch.setattr(Message, 'answer', AsyncMock())
    state = AsyncMock()
    state.get_state.return_value = 'AdminFlow:channel'
    def message(text):
        return Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=42,type='private'),from_user=User(id=42,is_bot=False,first_name='a'),text=text)
    handler = AsyncMock()
    gate = MembershipMiddleware()
    await gate(handler, message('/admin'), {'bot': object(), 'state':state})
    await gate(handler, message('@sponsor_channel'), {'bot':object(), 'state':state})
    assert handler.await_count == 2
    check.assert_not_awaited()
    await gate(handler, message('/start'), {'bot':object(), 'state':state})
    assert handler.await_count == 2
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_dispatcher_admin_private_guard(monkeypatch):
    from aiogram import Bot, Dispatcher, Router
    from aiogram.filters import Command
    from aiogram.types import Update
    from handlers import admin as panel
    monkeypatch.setattr(admin, 'authorized', lambda u: u == 42)
    monkeypatch.setattr(Message, 'answer', AsyncMock())
    home = AsyncMock()
    monkeypatch.setattr(panel, 'home', home)
    router = Router()
    router.message.middleware(AdminGuard())
    router.message.register(panel.entry, Command('admin'))
    dp = Dispatcher()
    dp.include_router(router)
    bot = Bot('123456:ABCDEFabcdef1234567890abcdef123456789')
    try:
        for uid in [7,42]:
            msg = Message(message_id=uid,date=datetime.now(timezone.utc),chat=Chat(id=uid,type='private'),from_user=User(id=uid,is_bot=False,first_name='a'),text='/admin')
            await dp.feed_update(bot, Update(update_id=uid,message=msg))
        home.assert_awaited_once()
    finally:
        await bot.session.close()
        await dp.storage.close()


@pytest.mark.asyncio
async def test_recharge_changes_only_target_quota_and_retains_history(db):
    from database.models import User as DbUser, AiRequest
    async with db() as s:
        s.add_all([DbUser(tg_user_id=9000000000000, quota_used=30, quota_date='2026-11-07',plan='free'),
                   DbUser(tg_user_id=2, quota_used=12,quota_date='2026-11-07')])
        await s.flush()
        s.add(AiRequest(request_key='old',user_id=9000000000000,source='business',state='succeeded'))
        await s.commit()
    assert await admin.recharge(42,9000000000000)
    async with db() as s:
        user = await s.get(DbUser,9000000000000)
        assert (user.quota_used,user.quota_date,user.plan) == (0,'2026-11-07','free')
        assert (await s.get(DbUser,2)).quota_used == 12
        assert (await s.get(AiRequest,'old')).state == 'succeeded'
        audit = (await s.scalars(select(AdminAudit))).one()
        assert audit.admin_id == 42 and audit.action == 'quota_recharge'
    assert await admin.recharge(42,9000000000000)  # safe to reset an already-zero counter


@pytest.mark.asyncio
async def test_recharge_unknown_and_unauthorized(db):
    from database.models import User as DbUser
    with pytest.raises(PermissionError):
        await admin.recharge(7,123)
    assert not await admin.recharge(42,123)
    async with db() as s:
        assert await s.get(DbUser,123) is None
        assert not (await s.scalars(select(AdminAudit))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize('argument',['','abc','-1','0','123 456','9223372036854775808','۴۲','1'*100])
async def test_recharge_bad_command_never_mutates(monkeypatch,argument):
    from handlers import admin as panel
    mutate = AsyncMock()
    monkeypatch.setattr(admin,'recharge',mutate)
    message = SimpleNamespace(text='/recharge '+argument,from_user=SimpleNamespace(id=42),answer=AsyncMock())
    await panel.recharge(message,AsyncMock())
    mutate.assert_not_awaited()
    assert '/recharge 123456789' in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_real_dispatcher_recharge_is_private_and_admin_only(monkeypatch):
    from aiogram import Bot, Dispatcher, Router
    from aiogram.filters import Command
    from aiogram.types import Update
    from handlers import admin as panel
    monkeypatch.setattr(admin,'authorized',lambda u:u==42)
    mutate = AsyncMock(return_value=True)
    monkeypatch.setattr(admin,'recharge',mutate)
    monkeypatch.setattr(Message,'answer',AsyncMock())
    router = Router()
    router.message.middleware(AdminGuard())
    router.message.register(panel.recharge,Command('recharge'))
    dp = Dispatcher()
    dp.include_router(router)
    bot = Bot('123456:ABCDEFabcdef1234567890abcdef123456789')
    try:
        for i,(uid,chat_id,kind) in enumerate([(7,7,'private'),(42,-1001,'supergroup'),(42,42,'private')]):
            msg = Message(message_id=i+1,date=datetime.now(timezone.utc),chat=Chat(id=chat_id,type=kind),
                          from_user=User(id=uid,is_bot=False,first_name='a'),text='/recharge 123456789')
            await dp.feed_update(bot,Update(update_id=i+1,message=msg))
        mutate.assert_awaited_once_with(42,123456789)
    finally:
        await bot.session.close()
        await dp.storage.close()


@pytest.mark.asyncio
async def test_recharge_admin_entry_does_not_require_membership(monkeypatch):
    from middleware.membership import MembershipMiddleware
    from services.membership import membership
    monkeypatch.setattr(admin,'authorized',lambda u:u==42)
    check = AsyncMock()
    monkeypatch.setattr(membership,'check',check)
    handler = AsyncMock()
    msg = Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=42,type='private'),
                  from_user=User(id=42,is_bot=False,first_name='a'),text='/recharge 123')
    await MembershipMiddleware()(handler,msg,{})
    handler.assert_awaited_once()
    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_requests_count_after_recharge_without_replaying_old_keys(db,monkeypatch):
    from services import store
    from services.quota import utc_today
    from database.models import User as DbUser, AiRequest
    monkeypatch.setattr(store,'get_session',admin.get_session)
    async with db() as s:
        s.add(DbUser(tg_user_id=123,quota_used=30,quota_date=utc_today()))
        await s.flush()
        s.add(AiRequest(request_key='old',user_id=123,source='business',state='succeeded'))
        await s.commit()
    assert await admin.recharge(42,123)
    assert await store.quota_hit_and_count(123,30,request_key='old') == (False,0)
    assert await store.quota_hit_and_count(123,30,request_key='new') == (True,1)
