from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import pytest
from aiogram.types import Message, Chat, User, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from middleware.rate_limit import RateLimitMiddleware, safety_request


def message(text='hello', uid=1):
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=uid,type='private'),
                   from_user=User(id=uid,is_bot=False,first_name='a'), text=text)


def callback(data='ui:home'):
    return CallbackQuery(id='x', from_user=message().from_user, chat_instance='x',message=message(),data=data)


@pytest.mark.asyncio
async def test_shared_message_callback_limit_and_ack(monkeypatch):
    now = [0.0]
    limiter = RateLimitMiddleware(clock=lambda: now[0])
    handler = AsyncMock(return_value='ok')
    ack = AsyncMock()
    monkeypatch.setattr(CallbackQuery, 'answer', ack)
    assert await limiter(handler, message(), {}) == 'ok'
    assert await limiter(handler, callback(), {}) is None
    ack.assert_awaited_once()
    now[0] = 0.8
    assert await limiter(handler, callback(), {}) == 'ok'
    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_capacity_expiry_and_rejected_requests_do_not_pin_keys():
    now = [0.0]
    limiter = RateLimitMiddleware(max_entries=2, ttl_seconds=2, clock=lambda: now[0])
    assert await limiter.admit(1)
    assert await limiter.admit(2)
    assert not await limiter.admit(3)
    now[0] = .5
    assert not await limiter.admit(1)
    now[0] = 2
    assert await limiter.admit(3)
    assert list(limiter._buckets[False]) == [3]
    now[0] = 4
    limiter.purge()
    assert not limiter._buckets[False]


@pytest.mark.asyncio
async def test_safety_has_independent_bounded_budget():
    limiter = RateLimitMiddleware(max_entries=1)
    assert await limiter.admit(1)
    assert await limiter.admit(1, safety=True)
    assert not await limiter.admit(1, safety=True)
    assert not await limiter.admit(2, safety=True)
    assert len(limiter._buckets[True]) == 1


@pytest.mark.asyncio
async def test_concurrent_admission_is_atomic():
    limiter = RateLimitMiddleware()
    assert sum(await asyncio.gather(*(limiter.admit(1) for _ in range(100)))) == 1


@pytest.mark.parametrize('text', ['/pause','/forget@filobot','/cancel arg'])
def test_safety_commands(text):
    assert safety_request(message(text))


def test_only_explicit_safety_actions():
    assert safety_request(callback('ui:forgetconfirm'))
    assert safety_request(callback('ui:pause'))
    assert not safety_request(callback('ui:resume'))
    assert not safety_request(message('/start'))
    assert not safety_request(message('please /forget'))


@pytest.mark.asyncio
async def test_expired_callback_never_reaches_handler(monkeypatch):
    limiter = RateLimitMiddleware()
    await limiter.admit((None,1))
    monkeypatch.setattr(CallbackQuery, 'answer', AsyncMock(side_effect=TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id='x'),message='query is too old')))
    handler = AsyncMock()
    await limiter(handler, callback(), {})
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_user_isolation():
    limiter = RateLimitMiddleware()
    handler = AsyncMock()
    for uid, bot_id in [(1,10),(2,10),(1,11)]:
        await limiter(handler, message(uid=uid), {'bot':SimpleNamespace(id=bot_id)})
    assert handler.await_count == 3


@pytest.mark.parametrize('kwargs', [{'limit_seconds':0},{'ttl_seconds':.1},{'max_entries':0},{'limit_seconds':float('nan')}])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        RateLimitMiddleware(**kwargs)
