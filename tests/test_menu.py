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
