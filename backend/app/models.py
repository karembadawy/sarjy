"""The six tables of product.md §10.

All six are defined now even though Phase 1 only writes to users / sessions / messages —
the schema is a locked decision, and creating it once avoids a second migration pass later.

Constrained string columns (`role`, `language`, `status`, `preferred_persona`) use
CHECK constraints rather than PostgreSQL ENUM types: same guarantee, but adding a value
later is one ALTER instead of a type migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Values allowed by the CHECK constraints — imported by the app so the two never drift.
PERSONAS = ("egyptian", "gulf")
LANGUAGES = ("ar", "en", "mixed")
ROLES = ("user", "assistant")
BOOKING_STATUSES = ("confirmed", "cancelled")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Base(DeclarativeBase):
    pass


class User(Base):
    """One browser = one user (D-011: UUID minted by the frontend, no authentication)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in_list("preferred_persona", PERSONAS), name="ck_users_persona"),
        CheckConstraint(
            f"preferred_language IS NULL OR {_in_list('preferred_language', LANGUAGES)}",
            name="ck_users_language",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    preferred_persona: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="egyptian", default="egyptian"
    )
    # NULL = mirror the user's language per turn (product.md §6.1). Set only by an
    # explicit switch such as "كلمني عربي" (§6.2) — wired in a later phase.
    preferred_language: Mapped[str | None] = mapped_column(String(8), nullable=True)

    sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")
    facts: Mapped[list["Fact"]] = relationship(back_populates="user")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")


class ChatSession(Base):
    """One page load / one call. Class renamed to avoid colliding with SQLAlchemy's Session."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(back_populates="session")


class Message(Base):
    """One turn of the transcript, labelled with its detected language (product.md §6.5)."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_in_list("role", ROLES), name="ck_messages_role"),
        CheckConstraint(_in_list("language", LANGUAGES), name="ck_messages_language"),
        # The brain's history query is exactly this: newest N rows of one session.
        Index("ix_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class Fact(Base):
    """Durable memory. Keys are canonical English snake_case regardless of input language (D-014)."""

    __tablename__ = "facts"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_facts_user_key"),
        CheckConstraint(
            f"source_language IS NULL OR {_in_list('source_language', LANGUAGES)}",
            name="ck_facts_source_language",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    # The language the fact was *told* in — a demo/writeup detail, not a lookup key.
    source_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="facts")


class Booking(Base):
    """A real row, created by the `create_booking` tool (D-013). Phase 4."""

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(_in_list("status", BOOKING_STATUSES), name="ck_bookings_status"),
        Index("ix_bookings_user_scheduled", "user_id", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    service: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="confirmed", default="confirmed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="bookings")


class TurnMetric(Base):
    """Per-turn stage timings (D-017). Written from Phase 2 on; nullable because a text-only
    turn has no speech stages and a failed stage should still record what it did measure."""

    __tablename__ = "turn_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speech_recognition_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brain_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voice_first_audio_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
