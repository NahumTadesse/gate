import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import ROOT
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from gate.db import Base


async def schema_diff(url: str) -> list[object]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(sync), Base.metadata
                )
            )
    finally:
        await engine.dispose()


def test_migrations_match_models_and_round_trip(test_database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = test_database_url
    config.attributes["configure_logger"] = False

    assert asyncio.run(schema_diff(test_database_url)) == []

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert asyncio.run(schema_diff(test_database_url)) == []
