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
    from services import schedule
    monkeypatch.setattr(schedule, "get_session", session)
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


@pytest.mark.asyncio
async def test_context_defaults_off_and_persona_edits_clear_cache(sessions):
    from services.context import recent_context
    p = await store.get_or_create_persona(1)
    assert p.context_enabled is False
    await store.update_persona(1, context_enabled=True)
    key, ticket, _ = recent_context.begin(1, "c", 2)
    recent_context.append(key, ticket, "user", "private")
    await store.update_persona(1, tone="صمیمی")
    assert key not in recent_context.sessions
    assert (await store.get_or_create_persona(1)).context_enabled is True


@pytest.mark.asyncio
async def test_edit_versions_prevent_stale_send_and_wrong_owner(sessions):
    from database.models import AiMessageLog
    await store.upsert_connection("edit", 1, {}, 1, True)
    async with sessions() as s:
        s.add(AiMessageLog(id=100, connection_id="edit", chat_id=2, message_id=3,
                           direction="draft", status="draft", draft_text="original"))
        await s.commit()
    assert await store.get_owned_pending_draft(100, 99, 0) is None
    assert not await store.edit_pending_draft(100, 99, 0, "attacker")
    assert await store.edit_pending_draft(100, 1, 0, "edited")
    assert not await store.edit_pending_draft(100, 1, 0, "stale edit")
    assert await store.claim_draft(100, 1) is None  # old unversioned button
    assert await store.claim_draft(100, 1, reject=True) is None
    row = await store.claim_draft(100, 1, expected_revision=1)
    assert row.draft_text == "edited"
    assert not await store.edit_pending_draft(100, 1, 1, "too late")
    assert await store.claim_draft(100, 1, expected_revision=1) is None


@pytest.mark.asyncio
async def test_edit_race_with_approval_has_only_one_winner(sessions):
    from database.models import AiMessageLog
    await store.upsert_connection("race", 1, {}, 1, True)
    async with sessions() as s:
        s.add(AiMessageLog(id=101, connection_id="race", chat_id=2, message_id=3,
                           direction="draft", status="draft", draft_text="original"))
        await s.commit()
    edited, claimed = await asyncio.gather(
        store.edit_pending_draft(101, 1, 0, "edited"), store.claim_draft(101, 1))
    assert bool(edited) != bool(claimed)
    if claimed:
        assert claimed.draft_text == "original"
    else:
        assert (await store.take_draft(101)).draft_text == "edited"


@pytest.mark.asyncio
async def test_expired_draft_cannot_send_or_edit_without_maintenance(sessions):
    from datetime import datetime, timedelta
    from database.models import AiMessageLog
    await store.upsert_connection("ttl", 1, {}, 1, True)
    async with sessions() as s:
        s.add(AiMessageLog(id=201, connection_id="ttl", chat_id=2, message_id=1,
                          direction="draft", status="draft", draft_text="old",
                          created_at=datetime.utcnow() - timedelta(hours=2)))
        await s.commit()
    assert await store.claim_draft(201, 1) is None
    assert await store.get_owned_pending_draft(201, 1, 0) is None
    assert not await store.edit_pending_draft(201, 1, 0, "extend")
    rows = await store.list_open_drafts(1)
    assert rows[0].status == "expired" and rows[0].draft_text is None


@pytest.mark.asyncio
async def test_maintenance_purges_without_making_uncertain_sendable(sessions, monkeypatch):
    from datetime import datetime, timedelta
    from database.models import AiMessageLog
    from services import draft_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "get_session", store.get_session)
    now = datetime.utcnow()
    await store.upsert_connection("purge", 1, {}, 1, True)
    async with sessions() as s:
        for id_, status, hours in [(210, "draft", 2), (211, "blocked", 25),
                                  (212, "uncertain", 25), (213, "sending", 25),
                                  (214, "sending", 0), (215, "draft", 0)]:
            s.add(AiMessageLog(id=id_, connection_id="purge", chat_id=2, message_id=id_,
                direction="draft", status=status, draft_text="private", created_at=now-timedelta(hours=hours)))
        await s.commit()
    await lifecycle.maintenance(now)
    assert (await store.take_draft(210)).status == "expired"
    for id_ in (210, 211, 212, 213):
        assert (await store.take_draft(id_)).draft_text is None
    assert (await store.take_draft(213)).status == "uncertain"
    assert (await store.take_draft(214)).status == "sending"
    assert (await store.take_draft(214)).draft_text == "private"
    assert (await store.take_draft(215)).status == "draft"
    assert await store.claim_draft(213, 1) is None
    await lifecycle.maintenance(now)  # idempotent


@pytest.mark.asyncio
async def test_close_and_bulk_reject_are_owned_and_do_not_touch_inflight(sessions, monkeypatch):
    from datetime import datetime, timedelta
    from database.models import AiMessageLog
    from services import draft_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "get_session", store.get_session)
    await store.upsert_connection("mine-close", 1, {}, 1, True)
    await store.upsert_connection("other-close", 2, {}, 2, True)
    before = datetime.utcnow()
    async with sessions() as s:
        for id_, connection, status, created in [
            (220, "mine-close", "draft", before-timedelta(seconds=1)),
            (221, "mine-close", "draft", before+timedelta(seconds=1)),
            (222, "mine-close", "sending", before),
            (223, "mine-close", "uncertain", before),
            (224, "other-close", "draft", before-timedelta(seconds=1))]:
            s.add(AiMessageLog(id=id_, connection_id=connection, chat_id=2, message_id=id_,
                direction="draft", status=status, draft_text="private", created_at=created))
        await s.commit()
    assert await lifecycle.reject_pending(1, before) == 1
    assert (await store.take_draft(220)).draft_text is None
    assert (await store.take_draft(221)).status == "draft"
    assert (await store.take_draft(224)).status == "draft"
    assert not await lifecycle.close_failed(2, 223)
    assert not await lifecycle.close_failed(1, 222)
    assert await lifecycle.close_failed(1, 223)
    assert (await store.take_draft(223)).status == "closed"
    assert (await store.take_draft(223)).draft_text is None


@pytest.mark.asyncio
async def test_chat_controls_owner_isolation_and_handoff(sessions, monkeypatch):
    from datetime import datetime, timedelta
    from services import chat_control as controls
    monkeypatch.setattr(controls, "get_session", store.get_session)
    await store.upsert_connection("chat-a", 1, {}, 1, True)
    await store.upsert_connection("chat-b", 2, {}, 2, True)
    assert await controls.ensure(2, "chat-a", 20) is None
    first = await controls.ensure(1, "chat-a", 20)
    second = await controls.ensure(1, "chat-a", 21)
    assert await controls.check(1, "chat-a", 20) == "active"
    assert not await controls.set_chat(2, first.id, True)
    await controls.set_chat(1, first.id, True)
    assert await controls.check(1, "chat-a", 20) == "chat_paused"
    assert await controls.check(1, "chat-a", 21) == "active"
    await controls.set_chat(1, first.id, False)
    await controls.owner_replied(1, "chat-a", 20, 100, 30)
    row = await controls.get_chat(1, first.id)
    assert controls.reason(row) == "owner_takeover"
    assert controls.reason(row, datetime.utcnow()+timedelta(minutes=31)) == "active"
    version = row.routing_revision
    until = row.handoff_until
    await controls.owner_replied(1, "chat-a", 20, 100, 30)  # retry must not extend
    again = await controls.get_chat(1, first.id)
    assert again.handoff_until == until and again.routing_revision == version
    await controls.set_chat(1, first.id, False)
    assert await controls.check(1, "chat-a", 20) == "active"
    assert (await controls.version(1, "chat-a", 20))[0] > version
    await store.delete_user_data(1)
    assert await controls.get_chat(1, first.id) is None
    assert await controls.get_chat(1, second.id) is None


@pytest.mark.asyncio
async def test_handoff_defaults_off_and_blocks_existing_pending(sessions, monkeypatch):
    from services import chat_control as controls
    from database.models import AiMessageLog
    monkeypatch.setattr(controls, "get_session", store.get_session)
    await store.upsert_connection("handoff", 1, {}, 1, True)
    assert (await store.get_or_create_persona(1)).handoff_minutes == 0
    row = await controls.ensure(1, "handoff", 20)
    await controls.owner_replied(1, "handoff", 20, 100, 0)
    assert await controls.check(1, "handoff", 20) == "active"
    async with sessions() as s:
        s.add(AiMessageLog(id=300, connection_id="handoff", chat_id=20, message_id=1,
                          direction="draft", status="draft", draft_text="pending"))
        await s.commit()
    await controls.owner_replied(1, "handoff", 20, 100, 15)
    assert (await store.take_draft(300)).status == "blocked"
    assert await store.claim_draft(300, 1) is None
    with pytest.raises(ValueError):
        await store.update_persona(1, handoff_minutes=999)


@pytest.mark.asyncio
async def test_schedule_is_default_off_owned_and_versioned(sessions, monkeypatch):
    from services import schedule, chat_control as controls
    monkeypatch.setattr(controls, "get_session", store.get_session)
    await store.upsert_connection("hours", 1, {}, 1, True)
    assert (await store.get_reply_schedule(1)).enabled is False
    with pytest.raises(ValueError):
        await store.update_reply_schedule(1, enabled=True)
    current = await store.update_reply_schedule(1, timezone="Asia/Tehran", days=[5,6,0,1,2])
    token = await controls.version(1, "hours", 20)
    await store.update_reply_schedule(1, enabled=True, expected_revision=current.revision)
    assert await controls.version(1, "hours", 20) != token
    with pytest.raises(ValueError):
        await store.update_reply_schedule(1, enabled=True, expected_revision=current.revision)
    assert await schedule.read(2) is None
    assert (await schedule.read(1)).enabled
    with pytest.raises(ValueError):
        await store.update_reply_schedule(1, days=[])
    assert (await schedule.read(1)).days == [5,6,0,1,2]
    await store.delete_user_data(1)
    assert await schedule.read(1) is None
