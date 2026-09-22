import pytest
from conftest import truncate_tables
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

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
    test_database_url: str,
) -> None:
    # No models exist yet, so the test brings its own table and drops it after.
    async with committed_sessionmaker() as setup:
        await setup.execute(text("CREATE TABLE commit_probe (id int)"))
        await setup.commit()
    try:
        async with committed_sessionmaker() as writer:
            await writer.execute(text("INSERT INTO commit_probe VALUES (1)"))
            await writer.commit()

        async with committed_sessionmaker() as reader:
            assert await reader.scalar(text("SELECT count(*) FROM commit_probe")) == 1

        engine = create_async_engine(test_database_url, poolclass=NullPool)
        try:
            await truncate_tables(engine)
        finally:
            await engine.dispose()

        async with committed_sessionmaker() as reader:
            assert await reader.scalar(text("SELECT count(*) FROM commit_probe")) == 0
    finally:
        async with committed_sessionmaker() as cleanup:
            await cleanup.execute(text("DROP TABLE commit_probe"))
            await cleanup.commit()
