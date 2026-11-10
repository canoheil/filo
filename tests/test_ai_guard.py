import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core.config import Settings
from services import ai_guard as module
from services.ai_guard import AIGuard
from handlers import secretary, menu


@pytest.fixture
def policy(monkeypatch):
    values = dict(AI_MAX_CONCURRENT=2, AI_OWNER_MAX_CONCURRENT=1,
                  AI_CHAT_INTERVAL_SECONDS=2, AI_OWNER_INTERVAL_SECONDS=1,
                  AI_GLOBAL_INTERVAL_SECONDS=.2, AI_GUARD_MAX_KEYS=2)
    cfg = SimpleNamespace(**values)
    monkeypatch.setattr(module, 'get_settings', lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_rates_shared_owner_global_and_expiry(policy):
    policy.AI_GUARD_MAX_KEYS = 10
    now = [0.0]
    guard = AIGuard(clock=lambda: now[0])
    async with guard.slot(1, 'chat') as a:
        assert a.allowed
    assert not (await guard.acquire(1, 'chat')).allowed
    assert (await guard.acquire(2, 'other')).reason == 'global_rate'
    now[0] = 1
    assert (await guard.acquire(1, 'chat')).reason == 'chat_rate'
    async with guard.slot(1, 'other') as a:
        assert a.allowed
    now[0] = 1.5
    assert (await guard.acquire(1, 'third')).reason == 'owner_rate'
    now[0] = 61
    async with guard.slot(2, 'third') as a:
        assert a.allowed
    assert len(guard.history['chat']) == 1


@pytest.mark.asyncio
async def test_concurrent_limits_and_release_on_error(policy):
    now = [0.0]
    guard = AIGuard(clock=lambda: now[0])
    with pytest.raises(RuntimeError):
        async with guard.slot(1, 'a') as a:
            assert a.allowed
            assert (await guard.acquire(1, 'b')).reason == 'owner_busy'
            now[0] = .2
            async with guard.slot(2, 'a') as b:
                assert b.allowed
                assert (await guard.acquire(3, 'a')).reason == 'global_busy'
            raise RuntimeError()
    assert guard.total == 0 and not guard.active


@pytest.mark.asyncio
async def test_cancellation_releases_lease(policy):
    guard = AIGuard()
    ready = asyncio.Event()
    async def work():
        async with guard.slot(1, 'a') as a:
            assert a.allowed
            ready.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(work())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert guard.total == 0 and not guard.active


@pytest.mark.asyncio
async def test_capacity_and_notice_bounds(policy):
    now = [0.0]
    guard = AIGuard(clock=lambda: now[0])
    for owner in [1,2]:
        async with guard.slot(owner, 'a') as a:
            assert a.allowed
        now[0] += 3
    assert (await guard.acquire(3, 'a')).reason == 'capacity'
    assert await guard.notice(1, 'quota')
    assert not await guard.notice(1, 'quota')
    assert await guard.notice(2, 'quota')
    assert not await guard.notice(3, 'quota')
    now[0] = 70
    assert await guard.notice(3, 'quota')
    async with guard.slot(3, 'a') as a:
        assert a.allowed


@pytest.mark.asyncio
async def test_business_rejected_before_inbound_membership_quota_ai(monkeypatch, isolated_ai_guard):
    guard = isolated_ai_guard
    monkeypatch.setattr(secretary.store, 'get_connection', AsyncMock(return_value=SimpleNamespace(user_id=1,is_enabled=True)))
    inbound, quota, generate, membership = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(secretary.store, 'log_inbound_once', inbound)
    monkeypatch.setattr(secretary.store, 'quota_hit_and_count', quota)
    monkeypatch.setattr(secretary.ai, 'generate_reply', generate)
    monkeypatch.setattr(secretary.membership, 'check', membership)
    msg = SimpleNamespace(business_connection_id='c',sender_business_bot=None,from_user=SimpleNamespace(id=2),
                          text='hello',caption=None,chat=SimpleNamespace(id=2),message_id=1)
    bot = AsyncMock()
    async with guard.slot(1, 'sandbox'):
        await secretary.on_business_message(msg, bot)
    for mock in [inbound,quota,generate,membership,bot.send_message]:
        mock.assert_not_awaited()
    assert guard.total == 0


@pytest.mark.asyncio
async def test_oversized_business_skips_before_reservation(monkeypatch):
    monkeypatch.setattr(secretary.store, 'get_connection', AsyncMock(return_value=SimpleNamespace(user_id=1,is_enabled=True)))
    quota = AsyncMock()
    monkeypatch.setattr(secretary.store, 'quota_hit_and_count', quota)
    msg = SimpleNamespace(business_connection_id='c',sender_business_bot=None,from_user=SimpleNamespace(id=2),
                          text='x'*6001,caption=None,chat=SimpleNamespace(id=2),message_id=1)
    await secretary.on_business_message(msg, AsyncMock())
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_slot_blocks_sandbox_without_consuming_quota(monkeypatch, isolated_ai_guard):
    monkeypatch.setattr(menu.store, 'get_or_create_user', AsyncMock(return_value=SimpleNamespace(use_case='business')))
    monkeypatch.setattr(menu.store, 'get_or_create_persona', AsyncMock(return_value=SimpleNamespace(name='فیلو',role='a',tone='b')))
    quota, generate = AsyncMock(), AsyncMock()
    monkeypatch.setattr(menu.store, 'quota_hit_and_count', quota)
    monkeypatch.setattr(menu.ai, 'generate_reply', generate)
    msg = SimpleNamespace(text='hello',from_user=SimpleNamespace(id=1),chat=SimpleNamespace(id=1),message_id=1,answer=AsyncMock())
    async with isolated_ai_guard.slot(1, 'business'):
        await menu.sandbox_reply(msg, AsyncMock())
    quota.assert_not_awaited()
    generate.assert_not_awaited()
    assert 'سهمیه مصرف نشد' in msg.answer.call_args.args[0]


@pytest.mark.parametrize('field,value', [('AI_MAX_CONCURRENT',0),('AI_GLOBAL_INTERVAL_SECONDS',float('inf')),('AI_GUARD_MAX_KEYS',0)])
def test_config_validation(field,value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field:value})


@pytest.mark.asyncio
async def test_forget_clears_metadata_without_releasing_active_work(policy):
    guard = AIGuard(clock=lambda: 0)
    async with guard.slot(1, 'a'):
        assert await guard.notice(1, 'quota')
        guard.forget(1)
        assert not guard.history['owner'] and not guard.history['chat'] and not guard.notices
        assert guard.total == 1
        assert (await guard.acquire(1, 'b')).reason == 'owner_busy'
    assert guard.total == 0


@pytest.mark.asyncio
async def test_sandbox_absolute_timeout_releases_slot_and_records_failure(monkeypatch, isolated_ai_guard):
    monkeypatch.setattr(menu, 'get_settings', lambda: SimpleNamespace(FREE_DAILY_LIMIT=30, AI_REQUEST_TIMEOUT_SECONDS=.01))
    monkeypatch.setattr(menu.store, 'get_or_create_user', AsyncMock(return_value=SimpleNamespace(use_case='business')))
    monkeypatch.setattr(menu.store, 'get_or_create_persona', AsyncMock(return_value=SimpleNamespace(name='فیلو',role='a',tone='b')))
    monkeypatch.setattr(menu.store, 'quota_hit_and_count', AsyncMock(return_value=(True,1)))
    monkeypatch.setattr(menu.persona_service, 'build_system_prompt', lambda *a, **kw: 'system')
    monkeypatch.setattr(menu, 'fingerprint', lambda *a: 'test', raising=False)
    finish = AsyncMock()
    monkeypatch.setattr(menu.usage, 'finish', finish)
    async def slow(*a, **kw):
        await asyncio.sleep(60)
    monkeypatch.setattr(menu.ai, 'generate_reply', slow)
    # Fingerprint reads actual persona attributes, so patch its defining module.
    from services import setup
    monkeypatch.setattr(setup, 'fingerprint', lambda *a: 'test')
    msg = SimpleNamespace(text='hello',from_user=SimpleNamespace(id=1),chat=SimpleNamespace(id=1),message_id=1,answer=AsyncMock())
    await menu.sandbox_reply(msg, AsyncMock())
    assert isinstance(finish.call_args.args[2], TimeoutError)
    assert isolated_ai_guard.total == 0
    assert 'ناموفق' in msg.answer.call_args.args[0]
