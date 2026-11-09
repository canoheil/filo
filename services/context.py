"""Bounded, process-local, opt-in context. Never persisted or logged."""
from collections import OrderedDict
from dataclasses import dataclass, field
import time


@dataclass
class Session:
    touched: float
    messages: list[dict[str, str]] = field(default_factory=list)


class RecentContext:
    def __init__(self, ttl=900, max_chats=256, max_messages=12, max_chars=12000, clock=time.monotonic):
        self.ttl, self.max_chats = ttl, max_chats
        self.max_messages, self.max_chars, self.clock = max_messages, max_chars, clock
        self.sessions = OrderedDict()

    def purge(self):
        now = self.clock()
        for key, session in list(self.sessions.items()):
            if now - session.touched >= self.ttl:
                del self.sessions[key]

    def begin(self, owner, connection, chat):
        self.purge()
        key = (owner, connection, chat)
        if key not in self.sessions:
            self.sessions[key] = Session(self.clock())
        self.sessions.move_to_end(key)
        while len(self.sessions) > self.max_chats:
            self.sessions.popitem(last=False)
        session = self.sessions[key]
        return key, session, [dict(message) for message in session.messages]

    def append(self, key, ticket, role, content):
        self.purge()
        if self.sessions.get(key) is not ticket:
            return  # revoked, expired or evicted while a request was in flight
        if role not in {"user", "assistant"}:
            raise ValueError("Invalid context role")
        ticket.touched = self.clock()
        ticket.messages.append({"role": role, "content": content[:2000]})
        while (len(ticket.messages) > self.max_messages or
               sum(len(m["content"]) for m in ticket.messages) > self.max_chars):
            ticket.messages.pop(0)

    def sent(self, owner, connection, chat, content):
        self.purge()
        key = (owner, connection, chat)
        ticket = self.sessions.get(key)
        if ticket is not None:
            self.append(key, ticket, "assistant", content)

    def clear_owner(self, owner):
        for key in list(self.sessions):
            if key[0] == owner:
                del self.sessions[key]

    def clear_chat(self, connection, chat):
        for key in list(self.sessions):
            if key[1:] == (connection, chat):
                del self.sessions[key]


recent_context = RecentContext()
