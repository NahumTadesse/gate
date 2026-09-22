import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from gate.config import Settings
from gate.main import create_app

Handler = Callable[[httpx.Request], httpx.Response]

ROOT = Path(__file__).resolve().parent.parent


class DatabaseTestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: str = (
        "postgresql+asyncpg://postgres:devpassword@localhost:5432/gate_test"
    )


async def create_database_if_missing(url: str) -> None:
    target = make_url(url)
    if not target.database:
        raise ValueError(f"no database name in {target!r}")
    # CREATE DATABASE has to run outside a transaction, from another database.
    admin = create_async_engine(
        target.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                name = connection.dialect.identifier_preparer.quote(target.database)
                await connection.execute(text(f"CREATE DATABASE {name}"))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The migrated test database; tests needing it skip if Postgres is down."""
    url = DatabaseTestSettings().test_database_url
    try:
        asyncio.run(create_database_if_missing(url))
    except (SQLAlchemyError, OSError) as exc:
        pytest.skip(f"test database unavailable: {exc!r}")

    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def db_session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is rolled back after the test.

    Commits made by the code under test only release a savepoint, so nothing
    reaches the database.
    """
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def upstream_requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def make_client(
    upstream_requests: list[httpx.Request],
) -> Iterator[Callable[[Handler], TestClient]]:
    clients: list[TestClient] = []

    def make(handler: Handler) -> TestClient:
        def record(request: httpx.Request) -> httpx.Response:
            upstream_requests.append(request)
            return handler(request)

        app = create_app(
            settings=Settings(upstream_base_url="http://upstream.test"),
            transport=httpx.MockTransport(record),
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
