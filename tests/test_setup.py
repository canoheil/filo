from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from handlers import setup, menu
from services.setup import fingerprint, readiness


def persona(**changes):
    data = dict(name="فیلو", role="دستیار", tone="مودب", language="fa",
                knowledge_base="ساعت کاری ۹ تا ۵", instructions="قیمت حدس نزن", signature="")
    return SimpleNamespace(**(data | changes))


def test_review_is_invalidated_by_settings_changes():
    p = persona()
    digest = fingerprint(p, "personal")
    progress = SimpleNamespace(tested_hash=digest, approved_hash=digest)
    user = SimpleNamespace(use_case="personal")
    checks = readiness(p, user, progress, [])
    assert checks["تأیید پاسخ توسط تو"]
    assert not checks["اتصال ثبت‌شده با مجوز پاسخ"]
    p.tone = "صمیمی"
    checks = readiness(p, user, progress, [])
    assert not checks["آزمایش تنظیمات فعلی"]
    assert not checks["تأیید پاسخ توسط تو"]
    assert digest != fingerprint(persona(), "business")


def test_readiness_does_not_equate_populated_fields_with_review():
    checks = readiness(persona(), SimpleNamespace(use_case="business"),
                       SimpleNamespace(tested_hash=None, approved_hash=None),
                       [SimpleNamespace(is_enabled=True, rights={"can_reply": False})])
    assert checks["شخصیت"]
    assert not checks["آزمایش تنظیمات فعلی"]
    assert not checks["اتصال ثبت‌شده با مجوز پاسخ"]


@pytest.mark.asyncio
async def test_wizard_resumes_and_keep_does_not_overwrite(monkeypatch):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1))
    progress = SimpleNamespace(completed=["use_case", "name"])
    monkeypatch.setattr(setup.store, "get_setup_progress", AsyncMock(return_value=progress))
    prompt = AsyncMock()
    monkeypatch.setattr(setup, "prompt", prompt)
    await setup.next_step(cb, state)
    prompt.assert_awaited_once_with(cb, state, "role")
    update, mark = AsyncMock(), AsyncMock()
    monkeypatch.setattr(setup.store, "get_or_create_persona", AsyncMock(return_value=persona()))
    monkeypatch.setattr(setup.store, "update_persona", update)
    monkeypatch.setattr(setup.store, "mark_setup_step", mark)
    monkeypatch.setattr(setup, "next_step", AsyncMock())
    await setup.navigate_setup(cb, state, "setup:keep:role")
    mark.assert_awaited_once_with(1, "role")
    update.assert_not_called()
    await state.storage.close()


@pytest.mark.asyncio
async def test_start_does_not_change_settings_if_live_rights_revoked(monkeypatch):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1), bot=AsyncMock())
    monkeypatch.setattr(setup.store, "list_user_connections", AsyncMock(return_value=[
        SimpleNamespace(is_enabled=True, connection_id="c")]))
    cb.bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True, rights=SimpleNamespace(can_reply=False))
    update = AsyncMock()
    monkeypatch.setattr(setup.store, "update_persona", update)
    monkeypatch.setattr(menu, "show", AsyncMock())
    await setup.navigate_setup(cb, state, "setup:start")
    update.assert_not_called()
    await state.storage.close()


@pytest.mark.asyncio
async def test_successful_start_selects_draft_mode_without_sending(monkeypatch):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    cb = SimpleNamespace(from_user=SimpleNamespace(id=1), bot=AsyncMock())
    monkeypatch.setattr(setup.store, "list_user_connections", AsyncMock(return_value=[
        SimpleNamespace(is_enabled=True, connection_id="c")]))
    cb.bot.get_business_connection.return_value = SimpleNamespace(
        user=SimpleNamespace(id=1), is_enabled=True, rights=SimpleNamespace(can_reply=True))
    update = AsyncMock()
    monkeypatch.setattr(setup.store, "update_persona", update)
    monkeypatch.setattr(menu, "show", AsyncMock())
    await setup.navigate_setup(cb, state, "setup:start")
    update.assert_awaited_once_with(1, auto_reply=False, is_paused=False)
    cb.bot.send_message.assert_not_called()
    await state.storage.close()
