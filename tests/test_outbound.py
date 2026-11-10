import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from aiogram.methods import SendMessage, CopyMessage, AnswerCallbackQuery, GetUpdates, GetChatMember
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.base import Base
from database.models import OutboundCooldown
from core.config import Settings
from services.outbound import OutboundLimiter, OutboundDeferred, broadcast_request


class Clock:
    now = 0.0
    def __call__(self):
        return self.now
    async def sleep(self, seconds):
        self.now += seconds


def limiter():
    clock = Clock()
    gate = OutboundLimiter(clock=clock,sleep=clock.sleep, settings=Settings(_env_file=None))
    return gate, clock


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from database import connection
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/outbound.db')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def session():
        async with factory() as s:
            yield s
    monkeypatch.setattr(connection, 'get_session', session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_shared_send_copy_and_perchat_spacing():
    gate, clock = limiter()
    backend = AsyncMock(return_value='ok')
    bot = SimpleNamespace(id=1)
    await gate(backend, bot, SendMessage(chat_id=1,text='a'))
    await gate(backend, bot, CopyMessage(chat_id=2,from_chat_id=1,message_id=1))
    assert clock.now >= .05
    await gate(backend, bot, SendMessage(chat_id=1,text='b'))
    assert clock.now >= 1.05
    assert backend.await_count == 3
    assert gate.waiters == gate.product_waiters == 0


@pytest.mark.asyncio
async def test_business_never_waits_or_calls_network_on_local_rejection():
    gate, clock = limiter()
    backend = AsyncMock()
    bot = SimpleNamespace(id=1)
    method = SendMessage(chat_id=1,text='a',business_connection_id='c')
    await gate(backend,bot,method)
    with pytest.raises(OutboundDeferred):
        await gate(backend,bot,method)
    assert clock.now == 0 and backend.await_count == 1


@pytest.mark.asyncio
async def test_broadcast_yields_to_product_waiters_without_sleeping():
    gate, clock = limiter()
    gate.product_waiters = 1
    token = broadcast_request.set(True)
    try:
        backend = AsyncMock()
        with pytest.raises(OutboundDeferred):
            await gate(backend,SimpleNamespace(id=1),CopyMessage(chat_id=1,from_chat_id=42,message_id=1))
        backend.assert_not_awaited()
        assert clock.now == 0
    finally:
        broadcast_request.reset(token)


@pytest.mark.asyncio
async def test_callback_fast_reject_and_polling_stays_available():
    gate, _ = limiter()
    gate.blocked_until = 100
    backend = AsyncMock()
    with pytest.raises(OutboundDeferred):
        await gate(backend,SimpleNamespace(id=1),AnswerCallbackQuery(callback_query_id='q'))
    await gate(backend,SimpleNamespace(id=1),GetUpdates())
    assert backend.await_count == 1


@pytest.mark.asyncio
async def test_actual_429_persisted_restored_and_never_retried(db):
    gate, _ = limiter()
    bot = SimpleNamespace(id=1)
    method = SendMessage(chat_id=1,text='a')
    error = TelegramRetryAfter(method=method,message='flood',retry_after=30)
    backend = AsyncMock(side_effect=error)
    await gate.restore()
    with pytest.raises(TelegramRetryAfter):
        await gate(backend,bot,method)
    backend.assert_awaited_once()
    async with db() as s:
        row = await s.get(OutboundCooldown,1)
        assert row.blocked_until > datetime.utcnow() + timedelta(seconds=25)
    restored, _ = limiter()
    await restored.restore()
    assert restored.remaining() > 25
    with pytest.raises(OutboundDeferred):
        await restored(backend,bot,SendMessage(chat_id=2,text='b'))
    backend.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_flood_also_blocks_future_sends(db):
    gate, _ = limiter()
    read = GetChatMember(chat_id=1,user_id=2)
    with pytest.raises(TelegramRetryAfter):
        await gate(AsyncMock(side_effect=TelegramRetryAfter(method=read,message='flood',retry_after=20)),SimpleNamespace(id=1),read)
    assert gate.remaining() == 20


@pytest.mark.asyncio
async def test_network_failure_never_retried():
    gate, _ = limiter()
    backend = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await gate(backend,SimpleNamespace(id=1),SendMessage(chat_id=1,text='a'))
    backend.assert_awaited_once()


@pytest.mark.asyncio
async def test_bounded_chats_expire_and_waiters_cancel():
    gate, clock = limiter()
    gate.settings.OUTBOUND_MAX_CHATS = 16
    for n in range(16):
        await gate(AsyncMock(),SimpleNamespace(id=1),SendMessage(chat_id=n+1,text='a'))
    with pytest.raises(OutboundDeferred):
        await gate.admit(SimpleNamespace(id=1),SendMessage(chat_id=99,text='a'))
    clock.now += 61
    gate.purge()
    assert not gate.chats
    await gate(AsyncMock(),SimpleNamespace(id=1),SendMessage(chat_id=99,text='a'))
    ready = asyncio.Event()
    async def wait(_):
        ready.set()
        await asyncio.Event().wait()
    gate.sleep = wait
    task = asyncio.create_task(gate.admit(SimpleNamespace(id=1),SendMessage(chat_id=99,text='b')))
    await ready.wait()
    assert gate.waiters == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.waiters == gate.product_waiters == 0


@pytest.mark.asyncio
async def test_wait_deadline_and_group_interval():
    gate, clock = limiter()
    backend = AsyncMock()
    await gate(backend,SimpleNamespace(id=1),SendMessage(chat_id=-100123,text='a'))
    with pytest.raises(OutboundDeferred):
        await gate(backend,SimpleNamespace(id=1),SendMessage(chat_id=-100123,text='b'))
    assert backend.await_count == 1
    clock.now = 3.2
    await gate(backend,SimpleNamespace(id=1),SendMessage(chat_id=-100123,text='b'))
    assert backend.await_count == 2


@pytest.mark.asyncio
async def test_aiogram_session_boundary_covers_bot_shortcuts():
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    gate, clock = limiter()
    session = AiohttpSession()
    backend = AsyncMock(return_value=SimpleNamespace(message_id=1))
    session.make_request = backend
    session.middleware(gate)
    bot = Bot('123456:ABCDEFabcdef1234567890abcdef123456789',session=session)
    try:
        await bot.send_message(1,'first')
        await bot.copy_message(1,2,3)
        assert clock.now >= 1.05
        assert backend.await_count == 2
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_flood_persistence_failure_keeps_ram_block_and_original_error(monkeypatch):
    from database import connection
    def broken():
        raise RuntimeError('SECRET')
    monkeypatch.setattr(connection,'get_session',broken)
    gate, _ = limiter()
    method = SendMessage(chat_id=1,text='x')
    error = TelegramRetryAfter(method=method,message='flood',retry_after=15)
    with pytest.raises(TelegramRetryAfter) as caught:
        await gate(AsyncMock(side_effect=error),SimpleNamespace(id=1),method)
    assert caught.value is error
    assert gate.remaining() == 15


@pytest.mark.asyncio
async def test_max_waiters_is_bounded():
    gate, _ = limiter()
    gate.settings.OUTBOUND_MAX_WAITERS = 1
    gate.waiters = 1
    gate.next_global = 10
    with pytest.raises(OutboundDeferred):
        await gate.admit(SimpleNamespace(id=1),SendMessage(chat_id=1,text='a'))
    assert gate.waiters == 1


@pytest.mark.asyncio
async def test_bulk_methods_charge_each_message():
    from aiogram.methods import CopyMessages
    gate, _ = limiter()
    await gate.admit(SimpleNamespace(id=1),CopyMessages(chat_id=1,from_chat_id=2,message_ids=[1,2,3]))
    assert gate.next_global == pytest.approx(.15)
    assert gate.chats[(1,'1')] == pytest.approx(2.1)


@pytest.mark.asyncio
async def test_inflight_bound_and_cancellation_release():
    gate, clock = limiter()
    gate.settings.OUTBOUND_MAX_INFLIGHT = 1
    ready = asyncio.Event()
    async def backend(bot, method):
        ready.set()
        await asyncio.Event().wait()
    bot = SimpleNamespace(id=1)
    task = asyncio.create_task(gate(backend,bot,SendMessage(chat_id=1,text='a')))
    await ready.wait()
    clock.now = 1
    with pytest.raises(OutboundDeferred):
        await gate(AsyncMock(),bot,SendMessage(chat_id=2,text='b',business_connection_id='c'))
    assert gate.inflight == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.inflight == 0
    await gate(AsyncMock(),bot,SendMessage(chat_id=2,text='b',business_connection_id='c'))


@pytest.mark.asyncio
async def test_shorter_flood_does_not_shorten_persisted_cooldown(db):
    gate, _ = limiter()
    await gate.restore()
    await gate.flood(30)
    await gate.flood(5)
    assert gate.remaining() == 30
    restored, _ = limiter()
    await restored.restore()
    assert restored.remaining() > 25


@pytest.mark.parametrize('field,value', [('OUTBOUND_MAX_INFLIGHT',0),('OUTBOUND_GLOBAL_INTERVAL_SECONDS',.001),('OUTBOUND_MAX_WAIT_SECONDS',float('inf'))])
def test_outbound_config_validation(field,value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None,**{field:value})
