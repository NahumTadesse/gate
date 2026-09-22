import pytest
from conftest import clear_tables
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from gate.db import Base
from gate.models import Membership, Organization, User

pytestmark = pytest.mark.anyio


async def test_session_runs_queries(db_session: AsyncSession) -> None:
    assert await db_session.scalar(text("SELECT 1")) == 1


async def test_commits_stay_inside_the_test_transaction(
    db_session: AsyncSession, test_database_url: str
) -> None:
    await db_session.execute(text("CREATE TABLE rollback_probe (id int)"))
    await db_session.commit()

    assert await db_session.scalar(text("SELECT to_regclass('rollback_probe')"))

    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as other:
            seen = await other.scalar(text("SELECT to_regclass('rollback_probe')"))
    finally:
        await engine.dispose()
    assert seen is None


async def test_committed_sessions_see_each_others_commits(
    committed_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_sessionmaker() as writer:
        writer.add(Organization(name="Acme"))
        await writer.commit()

    async with committed_sessionmaker() as reader:
        assert await reader.scalar(select(func.count()).select_from(Organization)) == 1


async def test_clear_tables_empties_every_table(
    committed_sessionmaker: async_sessionmaker[AsyncSession],
    test_database_url: str,
) -> None:
    async with committed_sessionmaker() as writer:
        user = User(email="ada@example.com", password_hash="x")
        writer.add(
            Membership(user=user, organization=Organization(name="A"), role="owner")
        )
        await writer.commit()

    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        await clear_tables(engine)
        async with engine.connect() as connection:
            names = (
                await connection.scalars(
                    text(
                        "SELECT tablename FROM pg_tables"
                        " WHERE schemaname = current_schema()"
                        " AND tablename <> 'alembic_version'"
                    )
                )
            ).all()
            # A table the migrations create but no model declares would be
            # skipped by clear_tables and leak rows between tests.
            assert set(names) == set(Base.metadata.tables)
            for name in names:
                count = await connection.scalar(text(f'SELECT count(*) FROM "{name}"'))
                assert count == 0, name
    finally:
        await engine.dispose()
