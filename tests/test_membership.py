from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from aiogram.types import Message, Chat, User, CallbackQuery
from core.config import Settings
from core.membership import required_channels
from services import membership as service
from middleware import membership as gate


def settings(channel="@filo_support"):
    return Settings(_env_file=None, SUPPORT_CHANNEL=channel)


@pytest.mark.parametrize("value", ["@filo_support", "https://t.me/filo_support", "https://t.me/filo_support/"])
def test_public_config(value):
    result = required_channels(value)
    assert result[0].chat_id == "@filo_support"
    assert result[0].join_url == "https://t.me/filo_support"


def test_private_and_disabled_config():
    result = required_channels("-1001234567890", "https://t.me/+abc123")
    assert result[0].chat_id == -1001234567890
    assert not required_channels("")
    for channel, url in [("-1001234567890", ""), ("https://t.me/+abc123", ""),
                         ("@filo_support", "https://evil.test/join"), ("", "https://t.me/filo_support")]:
        with pytest.raises(ValueError):
            required_channels(channel, url)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, BOT_TOKEN="SECRET", SUPPORT_CHANNEL="invalid")
    assert "SECRET" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, is_member, expected", [
    ("member", False, "member"), ("administrator", False, "member"), ("creator", False, "member"),
    ("restricted", True, "member"), ("restricted", False, "missing"), ("left", False, "missing"),
    ("kicked", False, "missing"), ("unknown", False, "unavailable")])
async def test_member_statuses(monkeypatch, status, is_member, expected):
    monkeypatch.setattr(service, "get_settings", settings)
    bot = SimpleNamespace(id=99, get_chat_member=AsyncMock(side_effect=[
        SimpleNamespace(status="administrator"), SimpleNamespace(status=status, is_member=is_member)]))
    result = await service.MembershipService().check(bot, 1, fresh=True)
    assert result.state == expected
    assert bot.get_chat_member.call_args.kwargs["user_id"] == 1


@pytest.mark.asyncio
async def test_admin_and_network_errors_are_not_nonmembership(monkeypatch):
    monkeypatch.setattr(service, "get_settings", settings)
    for response in [SimpleNamespace(status="member"), RuntimeError("PRIVATE")]:
        bot = SimpleNamespace(id=99, get_chat_member=AsyncMock())
        if isinstance(response, Exception):
            bot.get_chat_member.side_effect = response
        else:
            bot.get_chat_member.return_value = response
        result = await service.MembershipService().check(bot, 1)
        assert result.state == "unavailable"
        text, _ = service.prompt(result)
        assert "به معنی عضو نبودن" in text and "PRIVATE" not in text
        assert bot.get_chat_member.await_count == 1


@pytest.mark.asyncio
async def test_positive_cache_is_bounded_and_fresh_recheck_revokes(monkeypatch):
    monkeypatch.setattr(service, "get_settings", settings)
    now = [0]
    checks = service.MembershipService(ttl=30, max_entries=1, clock=lambda: now[0])
    bot = SimpleNamespace(id=99, get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")))
    assert (await checks.check(bot, 1)).state == "member"
    assert (await checks.check(bot, 1)).state == "member"
    assert bot.get_chat_member.await_count == 2
    await checks.check(bot, 2)
    assert len(checks._positive) == 1
    bot.get_chat_member.side_effect = [SimpleNamespace(status="administrator"), SimpleNamespace(status="left")]
    assert (await checks.check(bot, 2, fresh=True)).state == "missing"
    assert not checks._positive
    bot.get_chat_member.side_effect = None
    await checks.check(bot, 1)
    now[0] = 31
    await checks.check(bot, 1)
    assert bot.get_chat_member.await_count == 10


def test_copy_is_plural_even_for_one_channel():
    result = service.MembershipResult("missing", required_channels("@filo_support"))
    text, kb = service.prompt(result)
    assert "کانال‌های زیر" in text
    assert kb.inline_keyboard[0][0].text == "عضویت در کانال‌ها"
    assert kb.inline_keyboard[-1][0].callback_data == "membership:check"


def message(text="/start"):
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"),
                   from_user=User(id=1, is_bot=False, first_name="Test"), text=text)


def callback(data="membership:check"):
    return CallbackQuery(id="1", from_user=User(id=1, is_bot=False, first_name="Test"),
                         chat_instance="1", message=message(), data=data)


@pytest.mark.asyncio
async def test_start_missing_does_not_reach_product_and_clears_input(monkeypatch):
    monkeypatch.setattr(gate, "get_settings", settings)
    check = AsyncMock(return_value=service.MembershipResult("missing", settings().required_channels))
    monkeypatch.setattr(gate.membership, "check", check)
    answer, handler, state = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    await gate.MembershipMiddleware()(handler, message(), {"bot": object(), "state": state})
    handler.assert_not_called()
    state.clear.assert_awaited_once()
    assert check.call_args.kwargs["fresh"] is True
    assert "کانال‌های زیر" in answer.call_args.args[0]


@pytest.mark.asyncio
async def test_retry_always_checks_telegram_then_opens_home(monkeypatch):
    from handlers import menu
    monkeypatch.setattr(gate, "get_settings", settings)
    check = AsyncMock(return_value=service.MembershipResult("member"))
    monkeypatch.setattr(gate.membership, "check", check)
    monkeypatch.setattr(menu, "home", AsyncMock(return_value="dashboard"))
    answer, cb_answer, handler = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", cb_answer)
    await gate.MembershipMiddleware()(handler, callback(), {"bot": object(), "state": AsyncMock()})
    assert check.call_args.kwargs["fresh"] is True
    assert answer.call_args.args[0] == "dashboard"
    cb_answer.assert_awaited_once()
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_existing_buttons_are_gated_but_data_erasure_is_accessible(monkeypatch):
    monkeypatch.setattr(gate, "get_settings", settings)
    check = AsyncMock(return_value=service.MembershipResult("missing", settings().required_channels))
    monkeypatch.setattr(gate.membership, "check", check)
    monkeypatch.setattr(Message, "answer", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    handler = AsyncMock()
    await gate.MembershipMiddleware()(handler, callback("ui:home"), {"bot": object()})
    handler.assert_not_called()
    await gate.MembershipMiddleware()(handler, message("/forget"), {"bot": object()})
    await gate.MembershipMiddleware()(handler, callback("ui:forgetconfirm"), {"bot": object()})
    assert handler.await_count == 2
    assert check.await_count == 1


@pytest.mark.asyncio
async def test_disabled_configuration_preserves_existing_behavior(monkeypatch):
    monkeypatch.setattr(gate, "get_settings", lambda: settings(""))
    handler = AsyncMock(return_value="ok")
    assert await gate.MembershipMiddleware()(handler, message(), {}) == "ok"


@pytest.mark.asyncio
async def test_stale_retry_after_gate_disabled_still_opens_dashboard(monkeypatch):
    from handlers import menu
    monkeypatch.setattr(gate, "get_settings", lambda: settings(""))
    monkeypatch.setattr(service, "get_settings", lambda: settings(""))
    monkeypatch.setattr(menu, "home", AsyncMock(return_value="dashboard"))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    await gate.MembershipMiddleware()(AsyncMock(), callback(), {"bot": SimpleNamespace(id=99), "state": AsyncMock()})
    assert answer.call_args.args[0] == "dashboard"


@pytest.mark.asyncio
async def test_gate_handles_real_dispatcher_start_before_any_product_handler(monkeypatch):
    from aiogram import Bot, Dispatcher, Router
    from aiogram.types import Update
    monkeypatch.setattr(gate, "get_settings", settings)
    monkeypatch.setattr(service, "get_settings", settings)
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDE12345678")
    monkeypatch.setattr(bot, "get_chat_member", AsyncMock(side_effect=[
        SimpleNamespace(status="administrator"), SimpleNamespace(status="left")]))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    handler = AsyncMock()
    router = Router()
    async def product_handler(event):
        return await handler(event)
    router.message.register(product_handler)
    dp = Dispatcher()
    dp.message.outer_middleware(gate.MembershipMiddleware())
    dp.include_router(router)
    try:
        await dp.feed_update(bot, Update(update_id=100, message=message()))
        handler.assert_not_called()
        assert "کانال‌های زیر" in answer.call_args.args[0]
    finally:
        await dp.storage.close()
        await bot.session.close()
