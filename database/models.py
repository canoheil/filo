"""Secretary bot schema (v1).

Privacy note: we deliberately do NOT store message contents by default.
Only metadata (ids, direction, token counts) is persisted — see AiMessageLog.
Draft mode stores the draft text temporarily and purges it on approve/reject.
"""

from datetime import datetime

from sqlalchemy import String, BigInteger, Integer, Text, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    use_case: Mapped[str] = mapped_column(String(16), default="business")  # personal | business
    plan: Mapped[str] = mapped_column(String(16), default="free")          # free | pro | business
    quota_date: Mapped[str] = mapped_column(String(10), default="")        # YYYY-MM-DD (UTC)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BusinessConnection(Base):
    """One row per Chat Automation connection (max 1 per Telegram account)."""

    __tablename__ = "business_connections"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    connection_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_user_id"), index=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # the owner's user chat id
    rights: Mapped[dict | None] = mapped_column(JSON, nullable=True)           # BusinessBotRights snapshot
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)            # chat scope prefs chosen at connect
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Persona(Base):
    """Per-owner customizable secretary persona (the heart of the product)."""

    __tablename__ = "personas"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_user_id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="فیلو")
    role: Mapped[str] = mapped_column(String(255), default="دستیار من")      # e.g. "فروشندهٔ فروشگاه X"
    tone: Mapped[str] = mapped_column(String(120), default="مودب و رسمی")
    language: Mapped[str] = mapped_column(String(8), default="fa")
    knowledge_base: Mapped[str] = mapped_column(Text, default="")             # free-form facts owner gives us
    instructions: Mapped[str] = mapped_column(Text, default="")               # owner's custom rules
    signature: Mapped[str] = mapped_column(String(120), default="")           # e.g. "— سکرتر فروشگاه"
    auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)          # False => draft-for-approval (safe default)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)           # owner hit /pause
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AiMessageLog(Base):
    """Metadata only (plus short-lived draft text). Unique key = idempotency
    against Telegram webhook/update retries."""

    __tablename__ = "ai_message_log"
    __table_args__ = (
        UniqueConstraint("connection_id", "chat_id", "message_id", "direction", name="uq_ai_log_direction"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(String(255), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(8))        # in | out | draft
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | draft | sent | rejected | overflow | error
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
