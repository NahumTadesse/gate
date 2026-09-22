import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid_utils.compat import uuid7

from gate.db import Base

ROLES = ("owner", "admin", "member")


def created_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Relationships use lazy="raise_on_sql" because implicit lazy loads can't run
# under asyncio; queries have to ask for what they need with selectinload() etc.
# Unlike lazy="raise", objects already in the session still resolve.
# passive_deletes leaves child rows to the database's ON DELETE CASCADE instead
# of loading them just to delete them.


class User(Base):
    __tablename__ = "users"

    # UUIDv7 is time-ordered, so new rows land at the end of the primary key index.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint(f"role IN {ROLES!r}", name="role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise_on_sql")
    organization: Mapped[Organization] = relationship(
        back_populates="memberships", lazy="raise_on_sql"
    )


class AuthSession(Base):
    """A login session. Named to avoid confusion with SQLAlchemy's Session."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()

    user: Mapped[User] = relationship(back_populates="sessions", lazy="raise_on_sql")


# Money is stored as integer micros (millionths of a dollar) to avoid float
# rounding. Counters default on the server so raw SQL inserts and upserts from
# the usage pipeline get them too.


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        # Redundant with the primary key on its own; it exists as the target of
        # the composite foreign key on requests, which pins a key to its org.
        UniqueConstraint("id", "org_id", name="uq_api_keys_id_org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # The first 8 characters of the key, so it can be recognised in listings.
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    rpm_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )
    # None means the key has no monthly budget.
    monthly_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()

    organization: Mapped[Organization] = relationship(
        back_populates="api_keys", lazy="raise_on_sql"
    )


class RequestLog(Base):
    """One proxied request. Named to avoid confusion with HTTP request objects."""

    __tablename__ = "requests"
    __table_args__ = (
        # One constraint over both columns, so a request can't name a key that
        # belongs to a different org. org_id needs no FK of its own: api_keys
        # already guarantees the org exists.
        ForeignKeyConstraint(
            ["api_key_id", "org_id"],
            ["api_keys.id", "api_keys.org_id"],
            name="fk_requests_api_key_id_org_id_api_keys",
        ),
        Index("ix_requests_org_id_created_at", "org_id", text("created_at DESC")),
        Index(
            "ix_requests_api_key_id_created_at", "api_key_id", text("created_at DESC")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Idempotency key: the usage pipeline inserts with ON CONFLICT DO NOTHING.
    request_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    api_key_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    streamed: Mapped[bool] = mapped_column(nullable=False, server_default=false())
    created_at: Mapped[datetime] = created_at_column()


class UsageRollup(Base):
    """Hourly usage totals per key and model."""

    __tablename__ = "usage_rollups"
    __table_args__ = (
        # Epoch seconds rather than date_trunc('hour', ...), which depends on the
        # session time zone (and so is wrong for offsets like +05:30).
        CheckConstraint(
            "extract(epoch FROM bucket_start) % 3600 = 0", name="bucket_start_hour"
        ),
    )

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class ModelPrice(Base):
    """A model's price from effective_from until the next row for that model."""

    __tablename__ = "model_prices"

    model: Mapped[str] = mapped_column(Text, primary_key=True)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    input_micros_per_1k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_micros_per_1k: Mapped[int] = mapped_column(BigInteger, nullable=False)
