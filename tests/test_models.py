from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gate.models import AuthSession, Membership, Organization, User

pytestmark = pytest.mark.anyio


def make_user(email: str = "ada@example.com") -> User:
    return User(email=email, password_hash="hash")


def make_session(user: User, token_hash: str = "token") -> AuthSession:
    return AuthSession(
        token_hash=token_hash,
        user=user,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


async def test_ids_are_uuid7_and_created_at_is_set(db_session: AsyncSession) -> None:
    user = make_user()
    db_session.add(user)
    await db_session.flush()

    assert user.id.version == 7
    assert user.created_at.tzinfo is not None


async def test_email_is_unique_ignoring_case(db_session: AsyncSession) -> None:
    db_session.add(make_user("ada@example.com"))
    await db_session.flush()

    db_session.add(make_user("ADA@Example.com"))
    with pytest.raises(IntegrityError, match="uq_users_email"):
        await db_session.flush()


async def test_role_check_rejects_unknown_role(db_session: AsyncSession) -> None:
    db_session.add(
        Membership(user=make_user(), organization=Organization(name="Acme"), role="god")
    )
    with pytest.raises(IntegrityError, match="ck_memberships_role"):
        await db_session.flush()


async def test_membership_is_unique_per_user_and_org(
    db_session: AsyncSession,
) -> None:
    user, org = make_user(), Organization(name="Acme")
    db_session.add(Membership(user=user, organization=org, role="owner"))
    await db_session.flush()
    # A second object with the same key would be caught by the identity map, so
    # go around the ORM to check the primary key constraint itself.
    db_session.expunge_all()

    db_session.add(Membership(user_id=user.id, org_id=org.id, role="member"))
    with pytest.raises(IntegrityError, match="pk_memberships"):
        await db_session.flush()


async def test_deleting_user_cascades_to_memberships_and_sessions(
    db_session: AsyncSession,
) -> None:
    user, other = make_user(), make_user("grace@example.com")
    org = Organization(name="Acme")
    db_session.add_all(
        [
            Membership(user=user, organization=org, role="owner"),
            Membership(user=other, organization=org, role="member"),
            make_session(user, "t1"),
            make_session(user, "t2"),
            make_session(other, "t3"),
        ]
    )
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    memberships = await db_session.scalars(select(Membership.user_id))
    assert memberships.all() == [other.id]
    sessions = await db_session.scalars(select(AuthSession.token_hash))
    assert sessions.all() == ["t3"]
    assert await db_session.scalar(select(func.count()).select_from(Organization)) == 1


async def test_relationships_load(db_session: AsyncSession) -> None:
    user, org = make_user(), Organization(name="Acme")
    db_session.add_all(
        [Membership(user=user, organization=org, role="admin"), make_session(user)]
    )
    await db_session.flush()
    db_session.expunge_all()

    loaded_user = await db_session.scalar(
        select(User)
        .where(User.id == user.id)
        .options(
            selectinload(User.memberships).selectinload(Membership.organization),
            selectinload(User.sessions),
        )
    )
    assert loaded_user is not None
    [membership] = loaded_user.memberships
    assert membership.role == "admin"
    assert membership.organization.name == "Acme"
    assert [s.token_hash for s in loaded_user.sessions] == ["token"]
    assert loaded_user.sessions[0].user is loaded_user

    loaded_org = await db_session.scalar(
        select(Organization)
        .where(Organization.id == org.id)
        .options(selectinload(Organization.memberships).selectinload(Membership.user))
    )
    assert loaded_org is not None
    assert [m.user.email for m in loaded_org.memberships] == ["ada@example.com"]


async def test_unloaded_relationships_raise_instead_of_lazy_loading(
    db_session: AsyncSession,
) -> None:
    user = make_user()
    db_session.add(user)
    await db_session.flush()
    db_session.expunge_all()

    loaded = await db_session.get(User, user.id)
    assert loaded is not None
    with pytest.raises(Exception, match="lazy='raise_on_sql'"):
        _ = loaded.memberships
