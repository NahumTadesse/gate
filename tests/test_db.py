import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
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
