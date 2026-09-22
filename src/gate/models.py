import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
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
