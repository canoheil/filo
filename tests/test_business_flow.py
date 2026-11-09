from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import secretary


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["draft", "duplicate", "quota", "ai_error"])
async def test_message_pipeline(monkeypatch, scenario):
    conn = SimpleNamespace(user_id=1, is_enabled=True)
    persona = SimpleNamespace(is_paused=False, auto_reply=False)
    mocks = {
        "get_connection": AsyncMock(return_value=conn),
        "log_inbound_once": AsyncMock(return_value=scenario != "duplicate"),
        "get_or_create_persona": AsyncMock(return_value=persona),
        "get_or_create_user": AsyncMock(return_value=SimpleNamespace(tg_user_id=1)),
        "quota_hit_and_count": AsyncMock(return_value=(scenario != "quota", 1)),
        "save_draft": AsyncMock(return_value=10),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(secretary.store, name, mock)
    monkeypatch.setattr(secretary.persona_svc, "build_system_prompt", lambda _, **kw: "system")
    generate = AsyncMock(return_value="پاسخ *بدون قالب‌بندی*")
    if scenario == "ai_error":
        generate.side_effect = secretary.ai.AIError("http_503")
    monkeypatch.setattr(secretary.ai, "generate_reply", generate)
    message = SimpleNamespace(business_connection_id="c", sender_business_bot=None,
                              from_user=SimpleNamespace(id=2, first_name="مشتری"),
                              text="سلام", caption=None, chat=SimpleNamespace(id=2), message_id=3)
    bot = AsyncMock()
    await secretary.on_business_message(message, bot)
    if scenario == "draft":
        mocks["save_draft"].assert_awaited_once_with("c", 2, 3, "پاسخ *بدون قالب‌بندی*")
        assert bot.send_message.call_args.args[0] == 1  # owner, never customer
        assert bot.send_message.call_args.kwargs["parse_mode"] is None
        buttons = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0]
        assert buttons[0].callback_data == "draft:approve:10"
    else:
        mocks["save_draft"].assert_not_called()
    if scenario in {"duplicate", "quota"}:
        generate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "timeout", "rights", "paused"])
async def test_auto_pipeline(monkeypatch, mode):
    persona = SimpleNamespace(is_paused=False, auto_reply=True)
    monkeypatch.setattr(secretary.store, "get_connection", AsyncMock(
        return_value=SimpleNamespace(user_id=1, is_enabled=True)))
    monkeypatch.setattr(secretary.store, "log_inbound_once", AsyncMock(return_value=True))
    settings = AsyncMock(side_effect=[persona, SimpleNamespace(
        is_paused=mode == "paused", auto_reply=True), persona])
    monkeypatch.setattr(secretary.store, "get_or_create_persona", settings)
    monkeypatch.setattr(secretary.store, "get_or_create_user", AsyncMock(
        return_value=SimpleNamespace(tg_user_id=1)))
    monkeypatch.setattr(secretary.store, "quota_hit_and_count", AsyncMock(return_value=(True, 1)))
    save = AsyncMock(return_value=10)
    claim = AsyncMock(return_value=SimpleNamespace(connection_id="c", chat_id=2, draft_text="answer"))
    finish, state, log = AsyncMock(), AsyncMock(), AsyncMock()
    for name, mock in {"save_draft": save, "claim_draft": claim, "finish_draft": finish,
                       "set_draft_delivery_state": state, "log_outbound": log}.items():
        monkeypatch.setattr(secretary.store, name, mock)
    monkeypatch.setattr(secretary.persona_svc, "build_system_prompt", lambda _, **kw: "system")
    monkeypatch.setattr(secretary.ai, "generate_reply", AsyncMock(return_value="answer"))
    bot = AsyncMock()
    bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True,
        rights=SimpleNamespace(can_reply=mode != "rights"))
    bot.send_message.return_value = SimpleNamespace(message_id=4)
    if mode == "timeout":
        bot.send_message.side_effect = [TimeoutError(), SimpleNamespace(message_id=5)]
    message = SimpleNamespace(business_connection_id="c", sender_business_bot=None,
        from_user=SimpleNamespace(id=2), text="hello", caption=None,
        chat=SimpleNamespace(id=2), message_id=3)
    await secretary.on_business_message(message, bot)
    save.assert_awaited_once()
    if mode == "paused":
        claim.assert_not_called()
        bot.send_message.assert_not_called()
    elif mode == "success":
        finish.assert_awaited_once_with(10, "sent")
        assert bot.send_message.await_count == 1
    else:
        state.assert_awaited_once_with(10, "uncertain" if mode == "timeout" else "blocked")
        finish.assert_not_called()
        customer_sends = [c for c in bot.send_message.call_args_list if c.kwargs.get("chat_id") == 2]
        assert len(customer_sends) == (1 if mode == "timeout" else 0)


@pytest.mark.asyncio
async def test_optin_context_uses_previous_inbound_but_not_pending_draft(monkeypatch):
    from services.context import recent_context
    recent_context.clear_owner(1)
    p = SimpleNamespace(is_paused=False, auto_reply=False, context_enabled=True)
    monkeypatch.setattr(secretary.store, "get_connection", AsyncMock(return_value=SimpleNamespace(user_id=1, is_enabled=True)))
    monkeypatch.setattr(secretary.store, "get_or_create_persona", AsyncMock(return_value=p))
    monkeypatch.setattr(secretary.store, "get_or_create_user", AsyncMock(return_value=SimpleNamespace(tg_user_id=1)))
    monkeypatch.setattr(secretary.store, "quota_hit_and_count", AsyncMock(return_value=(True, 1)))
    monkeypatch.setattr(secretary.store, "log_inbound_once", AsyncMock(return_value=True))
    monkeypatch.setattr(secretary.store, "save_draft", AsyncMock(return_value=1))
    monkeypatch.setattr(secretary.persona_svc, "build_system_prompt", lambda _, **kw: "rules")
    generate = AsyncMock(return_value="UNSENT")
    monkeypatch.setattr(secretary.ai, "generate_reply", generate)
    message = SimpleNamespace(business_connection_id="c", sender_business_bot=None,
        from_user=SimpleNamespace(id=2, first_name="Test"), text="first", caption=None,
        chat=SimpleNamespace(id=2), message_id=1)
    try:
        await secretary.on_business_message(message, AsyncMock())
        message.text, message.message_id = "second", 2
        await secretary.on_business_message(message, AsyncMock())
        assert generate.call_args.kwargs["history"] == [{"role": "user", "content": "first"}]
        assert "UNSENT" not in str(recent_context.begin(1, "c", 2)[2])
    finally:
        recent_context.clear_owner(1)
