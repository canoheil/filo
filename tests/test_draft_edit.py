from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, Chat
from handlers import draft_edit
from core.draft_ui import draft_keyboard


@pytest_asyncio.fixture
async def state():
    storage = MemoryStorage()
    yield FSMContext(storage, StorageKey(bot_id=1, chat_id=1, user_id=1))
    await storage.close()


def test_revision_buttons_fit_and_legacy_means_zero():
    assert draft_keyboard(1).inline_keyboard[0][0].callback_data == "draft:approve:1"
    row = draft_keyboard(2**63 - 1, 2**31 - 1).inline_keyboard[0]
    assert all(len(button.callback_data.encode()) <= 64 for button in row)
    assert row[1].style == "primary"
    assert draft_keyboard(1, 2).inline_keyboard[0][0].callback_data == "draft:approve:1:2"


@pytest.mark.asyncio
async def test_other_private_chat_cannot_open_edit(state, monkeypatch):
    get = AsyncMock()
    monkeypatch.setattr(draft_edit.store, "get_owned_pending_draft", get)
    cb = SimpleNamespace(data="draft:edit:1:0", from_user=SimpleNamespace(id=1), answer=AsyncMock(),
        message=Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=2, type="private")))
    await draft_edit.begin_edit(cb, state)
    get.assert_not_called()
    assert await state.get_state() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("valid, saved", [(True, True), (True, False), (False, False)])
async def test_edit_requires_final_approval_and_validates_utf16(state, monkeypatch, valid, saved):
    await state.set_state(draft_edit.DraftEdit.text)
    await state.update_data(draft_id=1, revision=0)
    edit = AsyncMock(return_value=saved)
    claim = AsyncMock()
    monkeypatch.setattr(draft_edit.store, "edit_pending_draft", edit)
    monkeypatch.setattr(draft_edit.store, "claim_draft", claim)
    msg = SimpleNamespace(text="متن اصلاح‌شده" if valid else "🌱" * 2049,
                          from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await draft_edit.save_edit(msg, state)
    claim.assert_not_called()
    if not valid:
        edit.assert_not_called()
        assert await state.get_state() is not None
    else:
        assert await state.get_state() is None
        if saved:
            buttons = msg.answer.call_args.kwargs["reply_markup"].inline_keyboard[0]
            assert buttons[0].callback_data == "draft:approve:1:1"
            assert msg.answer.call_args.args[0] == msg.text
        else:
            assert "ذخیره نشد" in msg.answer.call_args.args[0]
