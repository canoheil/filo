import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from aiogram.methods import SendMessage
from services import protection
from services.protection import ProtectionMetrics, EVENTS, CAP
from services.ai_guard import AIGuard
from services.outbound import OutboundLimiter, OutboundDeferred
from middleware.rate_limit import RateLimitMiddleware
from core.config import Settings


@pytest.fixture
def monitor(monkeypatch):
    state = ProtectionMetrics(clock=lambda: 0)
    for name in ['middleware.rate_limit', 'services.ai_guard', 'services.outbound', 'services.protection']:
        import importlib
        monkeypatch.setattr(importlib.import_module(name), 'metrics', state)
    return state


def test_fixed_cardinality_saturation_and_window_expiry():
    now = [0]
    state = ProtectionMetrics(clock=lambda: now[0])
    for n in range(2000):
        state.record(f'user-secret-{n}')
    assert not state.total and not state.minutes
    for n in range(60):
        now[0] = n*60
        state.record('ai.accepted')
    assert len(state.minutes) == 15
    total, window, uptime = state.snapshot()
    assert total == {'ai.accepted':60} and window == {'ai.accepted':15}
    assert uptime == 3540
    state.total['ai.accepted'] = CAP
    state.record('ai.accepted')
    assert state.total['ai.accepted'] == CAP
    now[0] += 901
    assert state.snapshot()[1] == {}
    assert len(state.total) <= len(EVENTS)


@pytest.mark.asyncio
async def test_real_guards_emit_safe_reasons(monitor):
    guard = RateLimitMiddleware(max_entries=1,clock=lambda:0)
    assert await guard.admit('PRIVATE-ID')
    assert not await guard.admit('PRIVATE-ID')
    assert not await guard.admit('OTHER-ID')
    ai = AIGuard(clock=lambda:0)
    async with ai.slot(123,'PRIVATE-TEXT'):
        assert not (await ai.acquire(123,'another')).allowed
    assert await ai.notice(123,'quota')
    assert not await ai.notice(123,'quota')
    total,_,_ = monitor.snapshot()
    assert total['input.accepted'] == 1
    assert total['input.rate'] == total['input.capacity'] == 1
    assert total['ai.owner_busy'] == 1
    assert total['notice.allowed'] == total['notice.suppressed'] == 1
    assert 'PRIVATE' not in repr(monitor.__dict__)
    assert '123' not in repr(monitor.total)


@pytest.mark.asyncio
async def test_report_authorization_and_no_probe(monkeypatch,monitor):
    from services import admin, ai, outbound as out_module, ai_guard as ai_module
    monkeypatch.setattr(admin,'authorized',lambda u:u==42)
    generate = AsyncMock()
    monkeypatch.setattr(ai,'generate_reply',generate)
    monkeypatch.setattr(ai_module,'ai_guard',AIGuard(clock=lambda:0))
    monkeypatch.setattr(out_module,'outbound',OutboundLimiter(clock=lambda:0))
    monkeypatch.setattr(protection,'_input',RateLimitMiddleware(clock=lambda:0))
    monitor.record('out.flood')
    with pytest.raises(PermissionError):
        await protection.report(7)
    text = await protection.report(42)
    assert 'RetryAfter: 1 / 1' in text
    assert 'نه شمار مصرف AI' in text
    assert len(text.encode('utf-16-le'))//2 < 4096
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_panel_refresh_calls_authorized_report(monkeypatch):
    from handlers import admin as panel
    report = AsyncMock(return_value='safe report')
    monkeypatch.setattr(protection,'report',report)
    query = SimpleNamespace(data='adm:protection',answer=AsyncMock(),from_user=SimpleNamespace(id=42),message=SimpleNamespace(answer=AsyncMock()))
    await panel.callback(query,AsyncMock(),AsyncMock())
    report.assert_awaited_once_with(42)
    assert query.message.answer.call_args.args[0] == 'safe report'


@pytest.mark.asyncio
async def test_input_burst_10000_capacity_and_recovery(monitor):
    now = [0]
    guard = RateLimitMiddleware(max_entries=16,clock=lambda:now[0])
    admitted = await asyncio.gather(*(guard.admit(n) for n in range(10000)))
    assert sum(admitted) == 16 and len(guard._buckets[False]) == 16
    assert await guard.admit(99999,safety=True)
    now[0] = 61
    assert await guard.admit(99999)
    assert len(guard._buckets[False]) == 1
    total,_,_ = monitor.snapshot()
    assert total['input.capacity'] == 9984


@pytest.mark.asyncio
async def test_ai_burst_5000_bounded_and_recovers(monkeypatch,monitor):
    from services import ai_guard as module
    cfg = Settings(_env_file=None, AI_MAX_CONCURRENT=4,AI_GUARD_MAX_KEYS=16)
    monkeypatch.setattr(module,'get_settings',lambda:cfg)
    now = [0]
    guard = AIGuard(clock=lambda:now[0])
    # Open actual leases and hold them while a competing burst arrives.
    leases = []
    for owner in range(4):
        lease = guard.slot(owner,'a')
        assert (await lease.__aenter__()).allowed
        leases.append(lease)
        now[0] += 1
    denied = await asyncio.gather(*(guard.acquire(n+100,'a') for n in range(5000)))
    assert all(not a.allowed and a.reason=='global_busy' for a in denied)
    assert guard.total == 4 and len(guard.active) == 4
    for lease in leases:
        await lease.__aexit__(None,None,None)
    now[0] += 61
    async with guard.slot(99999,'b') as result:
        assert result.allowed
    assert guard.total == 0 and len(guard.history['chat']) == 1
    assert monitor.snapshot()[0]['ai.global_busy'] == 5000


@pytest.mark.asyncio
async def test_outbound_burst_1000_bounded_then_recovers(monitor):
    now = [0]
    cfg = Settings(_env_file=None,OUTBOUND_MAX_INFLIGHT=1,OUTBOUND_MAX_WAITERS=8)
    sleeping = set()
    async def sleep(_):
        sleeping.add(asyncio.current_task())
        await asyncio.Event().wait()
    gate = OutboundLimiter(clock=lambda:now[0],sleep=sleep,settings=cfg)
    gate.blocked_until = 10
    backend = AsyncMock()
    bot = SimpleNamespace(id=1)
    async def request(n):
        try:
            await gate(backend,bot,SendMessage(chat_id=n+1,text='not retained'))
        except OutboundDeferred:
            pass
    tasks = [asyncio.create_task(request(n)) for n in range(1000)]
    # Drain runnable tasks once: waiters block at fake sleep, excess fail fast.
    await asyncio.sleep(0)
    assert gate.waiters == 8 and len(sleeping) == 8
    backend.assert_not_awaited()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    assert gate.waiters == gate.product_waiters == gate.inflight == 0
    now[0] = 11
    await gate(backend,bot,SendMessage(chat_id=1,text='recovered'))
    backend.assert_awaited_once()
    assert monitor.snapshot()[0]['out.wait_full'] == 992
