import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from gate.auth import get_api_key
from gate.config import Settings
from gate.db import create_engine, create_sessionmaker
from gate.main import create_app
from gate.models import ApiKey

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


async def truncate_tables(engine: AsyncEngine) -> None:
    """Empty every table in the schema except Alembic's version bookkeeping."""
    async with engine.begin() as connection:
        names = (
            await connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables"
                    " WHERE schemaname = current_schema()"
                    " AND tablename <> 'alembic_version'"
                )
            )
        ).all()
        if names:
            quote = connection.dialect.identifier_preparer.quote
            tables = ", ".join(quote(name) for name in names)
            await connection.execute(
                text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
            )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The migrated test database.

    Tests needing it skip if Postgres is down, except in CI (the CI environment
    variable is set), where an unreachable database fails them instead.
    """
    url = DatabaseTestSettings().test_database_url
    try:
        asyncio.run(create_database_if_missing(url))
    except (SQLAlchemyError, OSError) as exc:
        message = f"test database unavailable: {exc!r}"
        if os.environ.get("CI"):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)

    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def db_session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is rolled back after the test.

    Commits made by the code under test only release a savepoint, so nothing
    reaches the database. This is the default for database tests: it is fast
    and needs no cleanup. It can't show behavior that depends on separate
    transactions (concurrent writers, SELECT ... FOR UPDATE blocking, constraint
    violations between sessions); use ``committed_sessionmaker`` for those.
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
async def committed_sessionmaker(
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker whose sessions commit for real, in separate transactions.

    Each session gets its own pooled connection, so a test can run several
    concurrently and see locking and isolation as production would. Tables are
    truncated before and after the test. It is slower than ``db_session``, so
    use it only for tests that need more than one transaction; everything else
    should use ``db_session``.
    """
    engine = create_engine(test_database_url)
    try:
        await truncate_tables(engine)
        yield create_sessionmaker(engine)
        await truncate_tables(engine)
    finally:
        await engine.dispose()


def skip_api_key_auth(app: FastAPI) -> FastAPI:
    """Accept every proxy request as coming from an active key, for proxy tests
    that aren't about authentication and so shouldn't need a database."""
    key = ApiKey(
        id=uuid7(), org_id=uuid7(), name="test", prefix="gk_test0", key_hash="test"
    )
    app.dependency_overrides[get_api_key] = lambda: key
    return app


@pytest.fixture
async def api_app(
    test_database_url: str,
    committed_sessionmaker: async_sessionmaker[AsyncSession],
    upstream_requests: list[httpx.Request],
) -> AsyncIterator[FastAPI]:
    """The whole app against the test database, with an upstream that records
    requests and answers 200.

    Requests commit for real, each in its own session as in production, so
    this builds on committed_sessionmaker (which also empties the tables around
    each test). Tests can use that fixture to inspect or adjust the database.
    """

    def record(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"id": "chatcmpl-test"})

    app = create_app(
        settings=Settings(
            database_url=test_database_url, upstream_base_url="http://upstream.test"
        ),
        transport=httpx.MockTransport(record),
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def make_api_client(
    api_app: FastAPI,
) -> AsyncIterator[Callable[[], httpx.AsyncClient]]:
    """Clients for api_app, each with its own cookie jar (i.e. its own user).

    These run on the test's event loop, unlike TestClient, so the app can share
    it with asyncpg. The https base URL matters: the session cookie is Secure
    and wouldn't be sent back over http.
    """
    async with AsyncExitStack() as stack:

        def make() -> httpx.AsyncClient:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api_app),
                base_url="https://testserver",
            )
            stack.push_async_callback(client.aclose)
            return client

        yield make


@dataclass
class Account:
    """A registered, logged-in user, with a client carrying their session."""

    client: httpx.AsyncClient
    user_id: str
    email: str
    org_id: str  # their personal org

    def org(self, path: str = "") -> str:
        return f"/api/v1/orgs/{self.org_id}{path}"


Signup = Callable[[str], Awaitable[Account]]


@pytest.fixture
def signup(make_api_client: Callable[[], httpx.AsyncClient]) -> Signup:
    """Registers and logs in <name>@example.com, owner of a personal org."""

    async def signup(name: str) -> Account:
        client = make_api_client()
        email = f"{name}@example.com"
        credentials = {"email": email, "password": "correct horse battery"}
        response = await client.post("/api/v1/auth/register", json=credentials)
        user_id = response.json()["id"]
        await client.post("/api/v1/auth/login", json=credentials)
        [org] = (await client.get("/api/v1/me")).json()["orgs"]
        return Account(client, user_id, email, org["id"])

    return signup


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
        client = TestClient(skip_api_key_auth(app))
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
