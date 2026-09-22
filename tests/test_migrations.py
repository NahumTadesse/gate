import anyio.to_thread
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import ROOT
from sqlalchemy.ext.asyncio import AsyncEngine

from gate.db import Base

pytestmark = pytest.mark.anyio


async def schema_diff(engine: AsyncEngine) -> list[object]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: compare_metadata(
                MigrationContext.configure(sync), Base.metadata
            )
        )


async def test_migrations_match_models_and_round_trip(
    test_database_url: str, test_engine: AsyncEngine
) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = test_database_url
    config.attributes["configure_logger"] = False

    def round_trip() -> None:
        command.downgrade(config, "base")
        command.upgrade(config, "head")

    assert await schema_diff(test_engine) == []

    # Alembic's env runs its own event loop, so it needs a thread of its own.
    await anyio.to_thread.run_sync(round_trip)
    # Recreating the schema gives types like citext new ids; pooled connections
    # hold prepared statements for the old ones, so start them over.
    await test_engine.dispose()

    assert await schema_diff(test_engine) == []
