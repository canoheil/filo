from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from handlers import insights, menu
from services import usage
from services.ai import AIError


def test_metadata_keys_and_error_whitelist():
    assert usage.business_key(1, "c", 2, 3) == usage.business_key(1, "c", 2, 3)
    assert usage.business_key(1, "c", 2, 3) != usage.business_key(2, "c", 2, 3)
    assert usage.sandbox_key(1, 1, 1) != usage.sandbox_key(1, 1, 2)
    assert usage.safe_error(AIError("http_401")) == "http_401"
    assert usage.safe_error(AIError("SECRET response body")) == "internal"
    assert usage.safe_error(RuntimeError("http_401")) == "internal"


@pytest.mark.asyncio
async def test_health_unknown_is_not_reported_as_confirmed_disconnection(monkeypatch):
    persona = SimpleNamespace(is_paused=True, auto_reply=False)
    user = SimpleNamespace(plan="free", quota_date="", quota_used=0)
    monkeypatch.setattr(insights.store, "get_or_create_persona", AsyncMock(return_value=persona))
    monkeypatch.setattr(insights.store, "get_or_create_user", AsyncMock(return_value=user))
    monkeypatch.setattr(insights.schedule, "read", AsyncMock(return_value=None))
    monkeypatch.setattr(insights.store, "list_user_connections", AsyncMock(return_value=[SimpleNamespace(connection_id="c")]))
    upsert = AsyncMock()
    monkeypatch.setattr(insights.store, "upsert_connection", upsert)
    show = AsyncMock()
    monkeypatch.setattr(menu, "show", show)
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1), bot=AsyncMock())
    cb.bot.get_business_connection.side_effect = RuntimeError("PRIVATE")
    await insights.show_health(cb)
    text = show.call_args.args[1]
    assert "اثبات قطع اتصال نیست" in text
    assert "PRIVATE" not in text
    assert "پیش‌نویس؛ ارسال نیازمند تأیید" in text
    cb.bot.send_message.assert_not_called()
    upsert.assert_not_called()


@pytest.mark.asyncio
async def test_health_only_refreshes_verified_owner_connections(monkeypatch):
    monkeypatch.setattr(insights.store, "get_or_create_persona", AsyncMock(return_value=SimpleNamespace(is_paused=False, auto_reply=True)))
    monkeypatch.setattr(insights.store, "get_or_create_user", AsyncMock(return_value=SimpleNamespace(plan="free", quota_date="", quota_used=0)))
    monkeypatch.setattr(insights.schedule, "read", AsyncMock(return_value=None))
    monkeypatch.setattr(insights.store, "list_user_connections", AsyncMock(return_value=[SimpleNamespace(connection_id="c")]))
    upsert = AsyncMock()
    monkeypatch.setattr(insights.store, "upsert_connection", upsert)
    monkeypatch.setattr(menu, "show", AsyncMock())
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1), bot=AsyncMock())
    cb.bot.get_business_connection.return_value = SimpleNamespace(user=SimpleNamespace(id=2), is_enabled=True)
    await insights.show_health(cb)
    upsert.assert_not_called()


@pytest.mark.asyncio
async def test_usage_ui_does_not_echo_unknown_error_content(monkeypatch):
    monkeypatch.setattr(insights.usage, "report", AsyncMock(return_value={
        "date": "2026-11-07", "requests": [("business", "succeeded", 2), ("sandbox", "failed", 1)],
        "sent": 1, "drafts": {"draft": 1}, "open": {"draft": 1}, "inbound": {}, "errors": {"PRIVATE": 1}}))
    monkeypatch.setattr(insights.store, "get_or_create_user", AsyncMock(return_value=SimpleNamespace(plan="free", quota_date="", quota_used=0)))
    show = AsyncMock()
    monkeypatch.setattr(menu, "show", show)
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1))
    await insights.show_usage(cb)
    text = show.call_args.args[1]
    assert "PRIVATE" not in text
    assert "رزرو درخواست AI ثبت‌شده: 3" in text
    assert "پاسخ واقعاً ارسال‌شده و ثبت‌شدهٔ امروز: 1" in text


@pytest.mark.asyncio
async def test_chat_inspection_rejects_other_owner_record(monkeypatch):
    get = AsyncMock(return_value=None)
    monkeypatch.setattr(insights.chat_control, "get_chat", get)
    show = AsyncMock()
    monkeypatch.setattr(menu, "show", show)
    cb = SimpleNamespace(from_user=SimpleNamespace(id=2), bot=AsyncMock())
    await insights.navigate(cb, "insights:chat:1")
    get.assert_awaited_once_with(2, 1)
    cb.bot.get_business_connection.assert_not_called()
    assert "در دسترس تو نیست" in show.call_args.args[1]
