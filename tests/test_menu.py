from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, Chat

from handlers import menu
from middleware.menu_state import CommandClearsMenuState


@pytest_asyncio.fixture
async def state():
    storage = MemoryStorage()
    yield FSMContext(storage, StorageKey(bot_id=9, chat_id=1, user_id=1))
    await storage.close()


def callback(action, chat_id=1):
    message = Message(message_id=1, date=datetime.now(timezone.utc),
                      chat=Chat(id=chat_id, type="private"))
    return SimpleNamespace(data=f"ui:{action}", from_user=SimpleNamespace(id=1),
                           message=message, answer=AsyncMock())


def test_keyboard_styles_and_callback_limits():
    kb = menu.home_keyboard()
    assert any(b.style == "primary" for row in kb.inline_keyboard for b in row)
    for row in kb.inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64
    assert menu.button("حذف", "forgetask", "danger").model_dump()["style"] == "danger"


@pytest.mark.asyncio
async def test_other_private_chat_cannot_change_settings(state, monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(menu.store, "update_persona", update)
    cb = callback("pause", chat_id=2)
    await menu.navigate(cb, state)
    update.assert_not_called()
    assert cb.answer.call_args.kwargs["show_alert"]


@pytest.mark.asyncio
async def test_delete_requires_single_use_confirmation(state, monkeypatch):
    delete, show = AsyncMock(), AsyncMock()
    monkeypatch.setattr(menu.store, "delete_user_data", delete)
    monkeypatch.setattr(menu, "show", show)
    await menu.navigate(callback("forgetconfirm"), state)
    delete.assert_not_called()
    await menu.navigate(callback("forgetask"), state)
    await menu.navigate(callback("forgetconfirm"), state)
    await menu.navigate(callback("forgetconfirm"), state)
    delete.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_navigation_cancels_edit(state, monkeypatch):
    monkeypatch.setattr(menu, "show", AsyncMock())
    await state.set_state(menu.EditPersona.value)
    await state.update_data(field="name")
    await menu.navigate(callback("help"), state)
    assert await state.get_state() is None
    assert await state.get_data() == {}


@pytest.mark.asyncio
async def test_auto_requires_confirmation_and_never_resumes(state, monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(menu.store, "update_persona", update)
    monkeypatch.setattr(menu, "home", AsyncMock(return_value="home"))
    monkeypatch.setattr(menu, "show", AsyncMock())
    await menu.navigate(callback("autoconfirm"), state)
    update.assert_not_called()
    await menu.navigate(callback("autoask"), state)
    await menu.navigate(callback("autoconfirm"), state)
    update.assert_awaited_once_with(1, auto_reply=True)


@pytest.mark.asyncio
async def test_edit_validates_without_silent_truncation(state, monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(menu.store, "update_persona", update)
    await state.set_state(menu.EditPersona.value)
    await state.update_data(field="name")
    message = SimpleNamespace(text="x" * 121, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await menu.save_edit(message, state)
    update.assert_not_called()
    assert await state.get_state() is not None
    message.text = "سارا"
    await menu.save_edit(message, state)
    update.assert_awaited_once_with(1, name="سارا")
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_commands_clear_pending_edit(state):
    await state.set_state(menu.EditPersona.value)
    await state.update_data(field="name")
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"), text="/pause")
    handler = AsyncMock()
    await CommandClearsMenuState()(handler, message, {"state": state})
    assert await state.get_state() is None
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_requires_confirmation_and_only_allowed_field(state, monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(menu.store, "update_persona", update)
    monkeypatch.setattr(menu, "home", AsyncMock(return_value="home"))
    monkeypatch.setattr(menu, "show", AsyncMock())
    await menu.navigate(callback("clear:knowledge_base"), state)
    update.assert_not_called()
    await menu.navigate(callback("clearask:knowledge_base"), state)
    await menu.navigate(callback("clear:knowledge_base"), state)
    update.assert_awaited_once_with(1, knowledge_base="")


@pytest.mark.asyncio
async def test_menu_is_registered_before_legacy_start(monkeypatch):
    from aiogram import Bot, Dispatcher
    from aiogram.types import Update, User
    from handlers import register_all_handlers
    # Use a fresh interpreter/router tree is unnecessary: this is the only dispatcher
    # registration in this test suite.
    dp = Dispatcher()
    register_all_handlers(dp)
    monkeypatch.setattr(menu, "home", AsyncMock(return_value="dashboard"))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDE12345678")
    try:
        await dp.feed_update(bot, Update(update_id=1, message=Message(
            message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="Test"), text="/start")))
        assert answer.call_args.args[0] == "dashboard"
        assert answer.call_args.kwargs["reply_markup"] == menu.home_keyboard()
    finally:
        await dp.storage.close()
        await bot.session.close()


def test_question_only_detection_is_conservative():
    from services.persona_health import is_question_only
    assert is_question_only("??? ????")
    assert not is_question_only("قیمت؟؟")
    assert not is_question_only("Sarah?")
    assert not is_question_only("")


@pytest.mark.asyncio
async def test_repair_requires_confirmation(state, monkeypatch):
    repair = AsyncMock(return_value=True)
    monkeypatch.setattr(menu.store, "reset_damaged_persona_field", repair)
    monkeypatch.setattr(menu, "show", AsyncMock())
    await menu.navigate(callback("repair:name"), state)
    repair.assert_not_called()
    await menu.navigate(callback("repairask:name"), state)
    await menu.navigate(callback("repair:name"), state)
    await menu.navigate(callback("repair:name"), state)
    repair.assert_awaited_once_with(1, "name")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "quota", "corrupt", "error", "long"])
async def test_sandbox_never_sends_or_saves_drafts(state, monkeypatch, mode):
    await state.set_state(menu.EditPersona.sandbox)
    persona = SimpleNamespace(name="???" if mode == "corrupt" else "فیلو", role="دستیار", tone="مودب", language="fa", knowledge_base="", instructions="", signature="")
    monkeypatch.setattr(menu.store, "get_or_create_user", AsyncMock(return_value=SimpleNamespace(use_case="business")))
    monkeypatch.setattr(menu.store, "record_setup_test", AsyncMock())
    monkeypatch.setattr(menu.store, "get_or_create_persona", AsyncMock(return_value=persona))
    quota = AsyncMock(return_value=(mode != "quota", 1))
    monkeypatch.setattr(menu.store, "quota_hit_and_count", quota)
    generate = AsyncMock(return_value="پاسخ آزمایشی")
    if mode == "error":
        generate.side_effect = RuntimeError("private")
    monkeypatch.setattr(menu.ai, "generate_reply", generate)
    monkeypatch.setattr(menu.persona_service, "build_system_prompt", lambda _, **kw: "prompt")
    save = AsyncMock()
    monkeypatch.setattr(menu.store, "save_draft", save)
    msg = SimpleNamespace(text="x" * 2001 if mode == "long" else "سلام", from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await menu.sandbox_reply(msg, state)
    save.assert_not_called()
    assert all("business_connection_id" not in call.kwargs for call in msg.answer.call_args_list)
    if mode in {"quota", "corrupt", "long"}:
        generate.assert_not_called()
    if mode in {"corrupt", "long"}:
        quota.assert_not_called()
    if mode == "ok":
        assert any("پاسخ آزمایشی" in call.args[0] for call in msg.answer.call_args_list)
    if mode == "error":
        assert all("private" not in call.args[0] for call in msg.answer.call_args_list)


@pytest.mark.asyncio
async def test_setup_start_requires_pending_confirmation(state, monkeypatch):
    from handlers import setup
    action = AsyncMock()
    monkeypatch.setattr(setup, "navigate_setup", action)
    await menu.navigate(callback("setup:start"), state)
    action.assert_not_called()
    await state.update_data(confirmation="setup:start")
    await menu.navigate(callback("setup:start"), state)
    action.assert_awaited_once()
    await menu.navigate(callback("setup:start"), state)
    assert action.await_count == 1


@pytest.mark.asyncio
async def test_context_optin_requires_confirmation_and_off_clears(state, monkeypatch):
    from services.context import recent_context
    update = AsyncMock()
    monkeypatch.setattr(menu.store, "update_persona", update)
    monkeypatch.setattr(menu, "show", AsyncMock())
    await menu.navigate(callback("context:on"), state)
    update.assert_not_called()
    await menu.navigate(callback("context:ask"), state)
    await menu.navigate(callback("context:on"), state)
    update.assert_awaited_once_with(1, context_enabled=True)
    key, ticket, _ = recent_context.begin(1, "c", 2)
    recent_context.append(key, ticket, "user", "private")
    await menu.navigate(callback("context:off"), state)
    assert key not in recent_context.sessions
    assert update.call_args.kwargs == {"context_enabled": False}


@pytest.mark.asyncio
async def test_lifecycle_confirmations_are_single_use(state, monkeypatch):
    from handlers import draft_lifecycle
    close, reject = AsyncMock(return_value=True), AsyncMock(return_value=2)
    monkeypatch.setattr(draft_lifecycle, "close_failed", close)
    monkeypatch.setattr(draft_lifecycle, "reject_pending", reject)
    monkeypatch.setattr(menu, "show", AsyncMock())
    await menu.navigate(callback("life:close:1"), state)
    close.assert_not_called()
    await menu.navigate(callback("life:closeask:1"), state)
    await menu.navigate(callback("life:close:1"), state)
    await menu.navigate(callback("life:close:1"), state)
    close.assert_awaited_once_with(1, 1)
    await menu.navigate(callback("life:rejectconfirm"), state)
    reject.assert_not_called()
    await menu.navigate(callback("life:rejectask"), state)
    await menu.navigate(callback("life:rejectconfirm"), state)
    await menu.navigate(callback("life:rejectconfirm"), state)
    reject.assert_awaited_once()
