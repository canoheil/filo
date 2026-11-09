from services.context import RecentContext


def test_isolation_limits_and_expiration():
    now = [0]
    memory = RecentContext(ttl=10, max_chats=2, max_messages=2, max_chars=10, clock=lambda: now[0])
    key, ticket, history = memory.begin(1, "a", 1)
    assert history == []
    memory.append(key, ticket, "user", "hello")
    memory.sent(1, "a", 1, "world")
    assert len(memory.begin(1, "a", 1)[2]) == 2
    assert memory.begin(1, "a", 2)[2] == []
    assert memory.begin(2, "a", 1)[2] == []
    assert len(memory.sessions) == 2  # LRU evicted first chat
    memory.append(key, ticket, "user", "late")
    assert key not in memory.sessions
    now[0] = 11
    memory.purge()
    assert not memory.sessions


def test_clear_revokes_inflight_ticket_and_owner_scope():
    memory = RecentContext()
    key, ticket, _ = memory.begin(1, "a", 1)
    other, other_ticket, _ = memory.begin(2, "a", 2)
    memory.clear_owner(1)
    memory.append(key, ticket, "user", "late")
    assert key not in memory.sessions
    assert other in memory.sessions
    memory.append(other, other_ticket, "user", "kept")
    memory.clear_chat("a", 2)
    assert not memory.sessions


def test_snapshot_is_copy_and_character_budget_enforced():
    memory = RecentContext(max_chars=6)
    key, ticket, _ = memory.begin(1, "a", 1)
    memory.append(key, ticket, "user", "abc")
    snapshot = memory.begin(1, "a", 1)[2]
    snapshot[0]["content"] = "changed"
    assert ticket.messages[0]["content"] == "abc"
    memory.append(key, ticket, "assistant", "12345")
    assert ticket.messages == [{"role": "assistant", "content": "12345"}]
