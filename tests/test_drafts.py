from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.base import Base
from database.models import User, BusinessConnection, AiMessageLog
from handlers.secretary import on_draft_action
from services import store


@pytest.mark.asyncio
async def test_claim_is_owned_and_single_use(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/drafts.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with factory() as s:
            yield s

    monkeypatch.setattr(store, "get_session", session)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            s.add(User(tg_user_id=1))
            s.add(BusinessConnection(connection_id="c", user_id=1))
            s.add(AiMessageLog(id=1, connection_id="c", chat_id=2, message_id=3,
                               direction="draft", status="draft", draft_text="private"))
            await s.commit()
        assert await store.claim_draft(1, 99) is None
        import asyncio
        results = await asyncio.gather(store.claim_draft(1, 1), store.claim_draft(1, 1))
        assert sum(row is not None for row in results) == 1
        await store.set_draft_delivery_state(1, "uncertain")
        assert (await store.take_draft(1)).draft_text == "private"
        assert await store.claim_draft(1, 1) is None
    finally:
        await engine.dispose()


def callback(data="draft:approve:1"):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=1),
                           answer=AsyncMock(), message=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["draft:x:1", "draft:approve:no", "draft:approve:0",
                                    "draft:approve:999999999999999999999999"])
async def test_invalid_callback(data, monkeypatch):
    claim = AsyncMock()
    monkeypatch.setattr(store, "claim_draft", claim)
    await on_draft_action(callback(data), AsyncMock())
    claim.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "blocked", "timeout", "reject", "not_owned"])
async def test_delivery(mode, monkeypatch):
    monkeypatch.setattr(store, "get_or_create_persona", AsyncMock(
        return_value=SimpleNamespace(is_paused=False)))
    row = SimpleNamespace(connection_id="c", chat_id=2, draft_text="hello *plain*")
    claim = AsyncMock(return_value=None if mode == "not_owned" else row)
    finish, state, log = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(store, "claim_draft", claim)
    monkeypatch.setattr(store, "finish_draft", finish)
    monkeypatch.setattr(store, "set_draft_delivery_state", state)
    monkeypatch.setattr(store, "log_outbound", log)
    bot = AsyncMock()
    bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=mode != "blocked",
        rights=SimpleNamespace(can_reply=True))
    bot.send_message.return_value = SimpleNamespace(message_id=5)
    if mode == "timeout":
        bot.send_message.side_effect = TimeoutError("sensitive")
    await on_draft_action(callback("draft:reject:1" if mode == "reject" else "draft:approve:1"), bot)
    if mode in {"blocked", "reject", "not_owned"}:
        bot.send_message.assert_not_called()
    elif mode == "timeout":
        state.assert_awaited_once_with(1, "uncertain")
        finish.assert_not_called()
    else:
        finish.assert_awaited_once_with(1, "sent")
        assert bot.send_message.call_args.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_recovery_ui_has_no_resend_for_uncertain(monkeypatch):
    from handlers.secretary import cmd_drafts
    rows = [SimpleNamespace(id=1, status="uncertain", draft_text="hidden"),
            SimpleNamespace(id=2, status="draft", draft_text="pending")]
    monkeypatch.setattr(store, "list_open_drafts", AsyncMock(return_value=rows))
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await cmd_drafts(message)
    assert message.answer.await_count == 3
    assert "hidden" not in message.answer.call_args_list[0].args[0]
    kb = message.answer.call_args_list[-1].kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "draft:approve:2"


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_context_records_only_successfully_sent_reply(monkeypatch, success):
    from handlers.secretary import _deliver_draft
    from services.context import recent_context
    recent_context.clear_owner(1)
    key, ticket, _ = recent_context.begin(1, "c", 2)
    recent_context.append(key, ticket, "user", "question")
    monkeypatch.setattr(store, "get_or_create_persona", AsyncMock(
        return_value=SimpleNamespace(is_paused=False, context_enabled=True)))
    monkeypatch.setattr(store, "finish_draft", AsyncMock())
    monkeypatch.setattr(store, "log_outbound", AsyncMock())
    monkeypatch.setattr(store, "set_draft_delivery_state", AsyncMock())
    bot = AsyncMock()
    bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True, rights=SimpleNamespace(can_reply=True))
    bot.send_message.return_value = SimpleNamespace(message_id=4)
    if not success:
        bot.send_message.side_effect = TimeoutError()
    row = SimpleNamespace(connection_id="c", chat_id=2, draft_text="answer")
    try:
        await _deliver_draft(row, 1, 1, bot)
        messages = recent_context.begin(1, "c", 2)[2]
        assert any(m["role"] == "assistant" for m in messages) == success
    finally:
        recent_context.clear_owner(1)


@pytest.mark.asyncio
async def test_deadline_rechecked_before_network_send(monkeypatch):
    from datetime import datetime, timedelta
    from handlers import secretary
    monkeypatch.setattr(store, "get_or_create_persona", AsyncMock(return_value=SimpleNamespace(is_paused=False)))
    expire = AsyncMock()
    monkeypatch.setattr(secretary, "expire_claim", expire)
    bot = AsyncMock()
    bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True, rights=SimpleNamespace(can_reply=True))
    row = SimpleNamespace(connection_id="c", chat_id=2, draft_text="old",
                          created_at=datetime.utcnow()-timedelta(hours=2))
    result = await secretary._deliver_draft(row, 1, 1, bot)
    assert "مهلت" in result
    expire.assert_awaited_once_with(1)
    bot.send_message.assert_not_called()


@pytest.fixture(autouse=True)
def routing_defaults(monkeypatch):
    from handlers import secretary
    monkeypatch.setattr(secretary.chat_control, "check", AsyncMock(return_value="active"))
    monkeypatch.setattr(secretary.chat_control, "note", AsyncMock())
    monkeypatch.setattr(secretary.chat_control, "version", AsyncMock(return_value=0))


@pytest.mark.asyncio
@pytest.mark.parametrize("routing", ["owner_takeover", "outside_hours"])
async def test_takeover_blocks_final_send(monkeypatch, routing):
    from handlers import secretary
    monkeypatch.setattr(secretary.chat_control, "check", AsyncMock(return_value=routing))
    monkeypatch.setattr(store, "get_or_create_persona", AsyncMock(return_value=SimpleNamespace(is_paused=False)))
    state = AsyncMock()
    monkeypatch.setattr(store, "set_draft_delivery_state", state)
    bot = AsyncMock()
    bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True, rights=SimpleNamespace(can_reply=True))
    await secretary._deliver_draft(SimpleNamespace(connection_id="c", chat_id=2, draft_text="answer"), 1, 1, bot)
    bot.send_message.assert_not_called()
    state.assert_awaited_once_with(1, "blocked")


@pytest.mark.asyncio
async def test_owner_membership_rechecked_before_delivery(monkeypatch):
    from handlers import secretary
    from services.membership import MembershipResult
    monkeypatch.setattr(secretary.membership, "check", AsyncMock(return_value=MembershipResult("missing")))
    monkeypatch.setattr(store, "get_or_create_persona", AsyncMock(return_value=SimpleNamespace(is_paused=False)))
    state = AsyncMock()
    monkeypatch.setattr(store, "set_draft_delivery_state", state)
    bot = AsyncMock()
    bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True, rights=SimpleNamespace(can_reply=True))
    notice = await secretary._deliver_draft(SimpleNamespace(connection_id="c", chat_id=2, draft_text="answer"), 1, 1, bot)
    assert "کانال‌ها" in notice
    bot.send_message.assert_not_called()
    state.assert_awaited_once_with(1, "blocked")
