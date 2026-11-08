import asyncio
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.base import Base
from database.models import User
from services import store
from services.quota import utc_today


@pytest_asyncio.fixture
async def sessions(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/store.db")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with factory() as s:
            yield s

    monkeypatch.setattr(store, "get_session", session)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_date,used", [("2000-01-01", 99), (None, 0), (None, 4)])
async def test_parallel_quota_respects_limit(sessions, old_date, used):
    async with sessions() as s:
        s.add(User(tg_user_id=1, quota_date=old_date or utc_today(), quota_used=used))
        await s.commit()
    results = await asyncio.gather(*(store.quota_hit_and_count(1, 5) for _ in range(12)))
    expected = 5 if old_date else 5 - used
    assert sum(allowed for allowed, _ in results) == expected
    async with sessions() as s:
        user = await s.get(User, 1)
        assert user.quota_date == utc_today()
        assert user.quota_used == 5


@pytest.mark.asyncio
async def test_quota_missing_paid_and_disabled(sessions):
    assert await store.quota_hit_and_count(404, 30) == (False, 0)
    async with sessions() as s:
        s.add(User(tg_user_id=1, plan="pro", quota_used=99))
        s.add(User(tg_user_id=2))
        await s.commit()
    assert await store.quota_hit_and_count(1, 0) == (True, 99)
    assert await store.quota_hit_and_count(2, 0) == (False, 0)


@pytest.mark.asyncio
async def test_persona_without_start_and_partial_updates(sessions):
    p = await store.update_persona(1, name="سارا")
    assert p.name == "سارا"
    p = await store.update_persona(1, tone="صمیمی")
    assert p.name == "سارا" and p.tone == "صمیمی"
    async with sessions() as s:
        assert await s.get(User, 1) is not None
    assert (await store.get_or_create_persona(2)).user_id == 2
    with pytest.raises(ValueError):
        await store.update_persona(1, user_id_override=9)


@pytest.mark.asyncio
async def test_connection_without_start(sessions):
    conn = await store.upsert_connection("c", 5, {"can_reply": True}, 5, True)
    assert conn.user_id == 5
    async with sessions() as s:
        assert await s.get(User, 5) is not None


@pytest.mark.asyncio
async def test_parallel_user_creation(sessions):
    users = await asyncio.gather(*(store.get_or_create_user(1, None) for _ in range(6)))
    assert all(user.tg_user_id == 1 for user in users)
    await store.get_or_create_user(1, "owner", use_case="personal")
    user = await store.get_or_create_user(1, None)
    assert user.username == "owner" and user.use_case == "personal"


@pytest.mark.asyncio
async def test_open_drafts_are_owner_scoped(sessions):
    from database.models import AiMessageLog
    await store.upsert_connection("mine", 1, {}, 1, True)
    await store.upsert_connection("other", 2, {}, 2, True)
    async with sessions() as s:
        for id_, conn, status in [(1, "mine", "draft"), (2, "mine", "uncertain"),
                                   (3, "other", "draft"), (4, "mine", "sent")]:
            s.add(AiMessageLog(id=id_, connection_id=conn, chat_id=9, message_id=id_,
                               direction="draft", status=status, draft_text="private"))
        await s.commit()
    assert [row.id for row in await store.list_open_drafts(1)] == [2, 1]
    assert await store.list_open_drafts(99) == []


@pytest.mark.asyncio
async def test_repair_preserves_custom_and_mixed_values(sessions):
    await store.update_persona(1, name="????", role="??? ???", tone="لحن؟")
    assert await store.reset_damaged_persona_field(1, "name")
    assert await store.reset_damaged_persona_field(1, "role")
    assert not await store.reset_damaged_persona_field(1, "tone")
    assert not await store.reset_damaged_persona_field(1, "name")
    p = await store.get_or_create_persona(1)
    assert p.name == "فیلو" and p.role == "دستیار من" and p.tone == "لحن؟"


@pytest.mark.asyncio
async def test_setup_progress_persists_and_delete_cleans_metadata(sessions):
    progress = await store.get_setup_progress(1)
    assert progress.completed == []
    await store.mark_setup_step(1, "name")
    await store.mark_setup_step(1, "name")
    assert (await store.get_setup_progress(1)).completed == ["name"]
    assert not await store.approve_setup_test(1, "a" * 64)
    await store.record_setup_test(1, "a" * 64)
    assert await store.approve_setup_test(1, "a" * 64)
    assert not await store.approve_setup_test(1, "b" * 64)
    await store.record_setup_test(1, "b" * 64)
    assert (await store.get_setup_progress(1)).approved_hash is None
    await store.delete_user_data(1)
    from database.models import SetupProgress
    async with sessions() as s:
        assert await s.get(SetupProgress, 1) is None
        assert await s.get(User, 1) is None
