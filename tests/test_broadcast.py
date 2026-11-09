from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from sqlalchemy import select, event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.methods import CopyMessage
from aiogram.types import Message, Chat, User
from database.base import Base
from database.models import BroadcastJob as Job, BroadcastTarget as Target, BroadcastSubscriber as Subscriber, User as DbUser, AdminAudit
from services import broadcast, store


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/broadcast.db')
    @event.listens_for(engine.sync_engine, 'connect')
    def fk(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def session():
        async with factory() as s:
            yield s
    monkeypatch.setattr(broadcast, 'get_session', session)
    monkeypatch.setattr(store, 'get_session', session)
    monkeypatch.setattr(broadcast, 'authorized', lambda u: u == 42)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


async def setup_job():
    await broadcast.subscribe(1)
    await broadcast.subscribe(2)
    return await broadcast.create(42, 123)


@pytest.mark.asyncio
async def test_preview_snapshot_confirmation_and_delivery(db):
    job_id = await setup_job()
    # Neither business-only user nor later private initiator is in this snapshot.
    async with db() as s:
        s.add(DbUser(tg_user_id=77))
        await s.commit()
    await broadcast.subscribe(3)
    assert await broadcast.claim() is None
    with pytest.raises(PermissionError):
        await broadcast.control(99, job_id, 'confirm')
    assert await broadcast.control(42, job_id, 'confirm')
    assert not await broadcast.control(42, job_id, 'confirm')
    bot = SimpleNamespace(copy_message=AsyncMock())
    await broadcast.step(bot)
    await broadcast.step(bot)
    await broadcast.step(bot)
    job, counts = await broadcast.report(42, job_id)
    assert job.state == 'completed' and counts == {'sent': 2}
    assert [c.kwargs['chat_id'] for c in bot.copy_message.call_args_list] == [1, 2]
    assert all(c.kwargs['message_id'] == 123 for c in bot.copy_message.call_args_list)
    async with db() as s:
        assert len((await s.scalars(select(AdminAudit))).all()) == 2


@pytest.mark.asyncio
async def test_stop_pending_and_inflight(db):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    item = await broadcast.claim()
    assert item[1] == 1
    assert await broadcast.control(42, job_id, 'stop')
    await broadcast.finish(job_id, 1, 'sent')
    assert await broadcast.claim() is None
    job, counts = await broadcast.report(42, job_id)
    assert job.state == 'stopped' and counts == {'sent': 1, 'skipped': 1}


@pytest.mark.asyncio
async def test_restart_never_retries_inflight(db):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    await broadcast.claim()
    await broadcast.recover()
    bot = SimpleNamespace(copy_message=AsyncMock())
    await broadcast.step(bot)
    await broadcast.step(bot)
    assert bot.copy_message.call_args.kwargs['chat_id'] == 2
    _, counts = await broadcast.report(42, job_id)
    assert counts == {'sent': 1, 'unknown': 1}


@pytest.mark.asyncio
@pytest.mark.parametrize('error,expected', [
    (TelegramForbiddenError(method=CopyMessage(chat_id=1,from_chat_id=42,message_id=123), message='PRIVATE'), 'blocked'),
    (TelegramBadRequest(method=CopyMessage(chat_id=1,from_chat_id=42,message_id=123), message='PRIVATE'), 'failed'),
    (TimeoutError('PRIVATE'), 'unknown'),
])
async def test_delivery_outcomes_are_safe(db, error, expected, caplog):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    bot = SimpleNamespace(copy_message=AsyncMock(side_effect=error))
    await broadcast.step(bot)
    _, counts = await broadcast.report(42, job_id)
    assert counts[expected] == 1
    assert 'PRIVATE' not in caplog.text
    if expected == 'blocked':
        async with db() as s:
            assert not (await s.get(Subscriber, 1)).enabled
        # A new private message proves the bot is reachable again.
        await broadcast.subscribe(1)
        async with db() as s:
            assert (await s.get(Subscriber, 1)).enabled


@pytest.mark.asyncio
async def test_retryafter_persists_global_cooldown_and_is_bounded(db):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    error = TelegramRetryAfter(method=CopyMessage(chat_id=1,from_chat_id=42,message_id=123), message='flood', retry_after=100)
    bot = SimpleNamespace(copy_message=AsyncMock(side_effect=error))
    for _ in range(5):
        await broadcast.step(bot)
        assert await broadcast.claim() is None
        async with db() as s:
            job = await s.get(Job, job_id)
            job.retry_at = datetime.utcnow() - timedelta(seconds=1)
            await s.commit()
    _, counts = await broadcast.report(42, job_id)
    assert counts == {'failed': 1, 'pending': 1}
    assert bot.copy_message.await_count == 5


@pytest.mark.asyncio
async def test_forget_removes_pending_targets_and_audience(db):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    await store.delete_user_data(1)
    bot = SimpleNamespace(copy_message=AsyncMock())
    await broadcast.step(bot)
    assert bot.copy_message.call_args.kwargs['chat_id'] == 2
    async with db() as s:
        assert await s.get(Subscriber, 1) is None
        assert await s.get(Target, (job_id, 1)) is None


@pytest.mark.asyncio
async def test_expiry_retention_and_removed_admin(db, monkeypatch):
    job_id = await setup_job()
    async with db() as s:
        job = await s.get(Job, job_id)
        job.created_at = datetime.utcnow() - timedelta(days=2)
        await s.commit()
    assert not await broadcast.control(42, job_id, 'confirm')
    await broadcast.maintenance()
    job, counts = await broadcast.report(42, job_id)
    assert job.state == 'expired' and counts == {'skipped': 2}
    async with db() as s:
        job = await s.get(Job, job_id)
        job.created_at = datetime.utcnow() - timedelta(days=31)
        await s.commit()
    await broadcast.maintenance()
    assert await broadcast.report(42, job_id) is None
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    monkeypatch.setattr(broadcast, 'authorized', lambda u: False)
    assert await broadcast.claim() is None
    async with db() as s:
        assert (await s.get(Job, job_id)).state == 'stopped'


@pytest.mark.asyncio
async def test_finalization_failure_not_retried(db, monkeypatch):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    original = broadcast.finish
    monkeypatch.setattr(broadcast, 'finish', AsyncMock(side_effect=RuntimeError('db down')))
    bot = SimpleNamespace(copy_message=AsyncMock())
    with pytest.raises(RuntimeError):
        await broadcast.step(bot)
    monkeypatch.setattr(broadcast, 'finish', original)
    await broadcast.step(bot)
    await broadcast.step(bot)
    assert bot.copy_message.await_count == 2
    _, counts = await broadcast.report(42, job_id)
    assert counts == {'sent': 1, 'unknown': 1}


def message(uid=1, text='hello', **kwargs):
    return Message(message_id=12,date=datetime.now(timezone.utc),chat=Chat(id=uid,type='private'),
                   from_user=User(id=uid,is_bot=False,first_name='a'),text=text,**kwargs)


@pytest.mark.asyncio
async def test_audience_only_private_messages_and_not_erasure(monkeypatch):
    from middleware.broadcast_audience import BroadcastAudienceMiddleware
    from middleware import broadcast_audience
    sub = AsyncMock()
    monkeypatch.setattr(broadcast_audience, 'subscribe', sub)
    gate = BroadcastAudienceMiddleware()
    handler = AsyncMock()
    for msg in [message(), message(text='/forget'), message(text='/forget@filo'),
                message().model_copy(update={'chat':Chat(id=-123,type='supergroup')}),
                message(business_connection_id='business')]:
        await gate(handler, msg, {})
    sub.assert_awaited_once_with(1)
    assert handler.await_count == 5


def test_payload_validation():
    assert broadcast.supported(message())
    assert not broadcast.supported(message(media_group_id='album'))
    assert not broadcast.supported(message(has_protected_content=True))


@pytest.mark.asyncio
async def test_prepare_uses_bot_preview_not_mutable_original(monkeypatch):
    from handlers import admin as panel
    create = AsyncMock(return_value=7)
    show = AsyncMock()
    monkeypatch.setattr(broadcast, 'create', create)
    monkeypatch.setattr(panel, 'show_broadcast', show)
    bot = SimpleNamespace(copy_message=AsyncMock(return_value=SimpleNamespace(message_id=99)))
    state = AsyncMock()
    await panel.prepare_broadcast(message(uid=42), state, bot)
    create.assert_awaited_once_with(42, 99)
    show.assert_awaited_once()
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_stopping_campaign_does_not_bypass_flood_cooldown(db):
    first = await setup_job()
    await broadcast.control(42, first, 'confirm')
    item = await broadcast.claim()
    await broadcast.finish(first, item[1], 'retry', 100)
    await broadcast.control(42, first, 'stop')
    second = await broadcast.create(42, 456)
    await broadcast.control(42, second, 'confirm')
    assert await broadcast.claim() is None
    async with db() as s:
        job = await s.get(Job, first)
        job.retry_at = datetime.utcnow() - timedelta(seconds=1)
        await s.commit()
    assert (await broadcast.claim())[0] == second


@pytest.mark.asyncio
async def test_canceled_retry_does_not_return_to_queue(db):
    job_id = await setup_job()
    await broadcast.control(42, job_id, 'confirm')
    item = await broadcast.claim()
    await broadcast.control(42, job_id, 'stop')
    await broadcast.finish(job_id, item[1], 'retry', 10)
    _, counts = await broadcast.report(42, job_id)
    assert counts == {'skipped': 2}


@pytest.mark.asyncio
async def test_another_admin_cannot_confirm_creators_draft(db, monkeypatch):
    job_id = await setup_job()
    monkeypatch.setattr(broadcast, 'authorized', lambda u: u in {42,43})
    assert not await broadcast.control(43, job_id, 'confirm')
    assert await broadcast.control(43, job_id, 'stop')


@pytest.mark.asyncio
async def test_service_rejects_all_unauthorized_admin_operations(db):
    job_id = await setup_job()
    for action in [broadcast.create(7, 456), broadcast.report(7, job_id), broadcast.recent(7)]:
        with pytest.raises(PermissionError):
            await action
