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
from argon2 import PasswordHasher, profiles
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

from gate import (
    models,  # noqa: F401  (registers the tables on Base.metadata)
    security,
)
from gate.auth import get_api_key
from gate.config import Settings
from gate.db import Base, create_sessionmaker
from gate.main import create_app
from gate.models import ApiKey
from gate.usage import UsageRecorder, get_usage_recorder

Handler = Callable[[httpx.Request], httpx.Response]

ROOT = Path(__file__).resolve().parent.parent


TEST_SECRET_KEY = "test-secret-key-" + "x" * 32


@pytest.fixture(autouse=True, scope="session")
def secret_key_env() -> Iterator[None]:
    """Run tests as production does: a SECRET_KEY set, and DEV not."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SECRET_KEY", TEST_SECRET_KEY)
        patch.delenv("DEV", raising=False)
        yield


@pytest.fixture(autouse=True, scope="session")
def cheap_password_hashing() -> Iterator[None]:
    """Hash passwords with argon2's cheapest parameters during tests.

    Production's parameters cost ~180ms per hash on purpose, which dominated
    the suite's runtime. The algorithm (argon2id) and code path are unchanged;
    only the work factors drop, and only here.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            security, "_hasher", PasswordHasher.from_parameters(profiles.CHEAPEST)
        )
        security._dummy_hash.cache_clear()
        yield
    security._dummy_hash.cache_clear()


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


async def clear_tables(engine: AsyncEngine) -> None:
    """Delete every row from the models' tables, children before parents.

    This runs around every committed-state test, so it's built to be cheap:
    DELETE rather than TRUNCATE (which replaces each table's files, ~85ms even
    for tiny tables), and all of them sent as one simple query, one round trip.
    """
    statements = "; ".join(
        f'DELETE FROM "{table.name}"' for table in reversed(Base.metadata.sorted_tables)
    )
    async with engine.connect() as connection:
        driver = (await connection.get_raw_connection()).driver_connection
        assert driver is not None
        # A multi-statement simple query runs as a single implicit transaction.
        await driver.execute(statements)


@pytest.fixture(scope="session")
def test_database_url(worker_id: str) -> str:
    """The migrated test database.

    Each pytest-xdist worker gets a database of its own (gate_test_gw0, ...),
    created and migrated on first use, so parallel tests can't see or delete
    each other's rows. Without xdist the configured name is used as is.

    Tests needing it skip if Postgres is down, except in CI (the CI environment
    variable is set), where an unreachable database fails them instead.
    """
    configured = make_url(DatabaseTestSettings().test_database_url)
    if worker_id != "master":
        configured = configured.set(database=f"{configured.database}_{worker_id}")
    url = configured.render_as_string(hide_password=False)
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


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    # Session-scoped so the whole run shares one event loop, which lets the
    # engine below (whose connections belong to a loop) be shared too.
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """One pooled engine for the whole run.

    Opening a connection costs ~60ms here (auth plus SQLAlchemy's startup
    queries), and asyncpg caches prepared statements per connection, so reusing
    connections across tests is most of what keeps the suite fast.
    """
    # No pool_pre_ping, unlike production (it costs a round trip per checkout):
    # nothing here drops connections behind the pool's back, and the migration
    # test, which invalidates them, resets the pool itself.
    engine = create_async_engine(test_database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is rolled back after the test.

    Commits made by the code under test only release a savepoint, so nothing
    reaches the database. This is the default for database tests: it is fast
    and needs no cleanup. It can't show behavior that depends on separate
    transactions (concurrent writers, SELECT ... FOR UPDATE blocking, constraint
    violations between sessions); use ``committed_sessionmaker`` for those.
    """
    async with test_engine.connect() as connection:
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


@pytest.fixture
async def committed_sessionmaker(
    test_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker whose sessions commit for real, in separate transactions.

    Each session gets its own pooled connection, so a test can run several
    concurrently and see locking and isolation as production would. Tables are
    emptied before and after the test. It is slower than ``db_session``, so
    use it only for tests that need more than one transaction; everything else
    should use ``db_session``.
    """
    await clear_tables(test_engine)
    yield create_sessionmaker(test_engine)
    await clear_tables(test_engine)


@dataclass
class RecordedCall:
    key: ApiKey
    model: str
    status_code: int
    usage: object
    streamed: bool


class FakeUsageRecorder(UsageRecorder):
    """Keeps what it's asked to record in a list instead of the database."""

    def __init__(self, calls: list[RecordedCall]) -> None:
        self.calls = calls

    async def record(
        self,
        *,
        key: ApiKey,
        model: str,
        status_code: int,
        usage: object,
        streamed: bool,
    ) -> None:
        self.calls.append(RecordedCall(key, model, status_code, usage, streamed))


def skip_api_key_auth(
    app: FastAPI, recorded: list[RecordedCall] | None = None
) -> FastAPI:
    """Accept every proxy request as coming from an active key, for proxy tests
    that aren't about authentication and so shouldn't need a database.

    Usage recording needs the database too, so it goes to `recorded` instead.
    """
    key = ApiKey(
        id=uuid7(), org_id=uuid7(), name="test", prefix="gk_test0", key_hash="test"
    )
    calls = [] if recorded is None else recorded
    app.dependency_overrides[get_api_key] = lambda: key
    app.dependency_overrides[get_usage_recorder] = lambda: FakeUsageRecorder(calls)
    return app


@dataclass
class Upstream:
    """The provider behind api_app. Tests can swap its handler."""

    handler: Handler = lambda _: httpx.Response(200, json={"id": "chatcmpl-test"})


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
async def api_app(
    test_database_url: str,
    test_engine: AsyncEngine,
    committed_sessionmaker: async_sessionmaker[AsyncSession],
    upstream: Upstream,
    upstream_requests: list[httpx.Request],
) -> AsyncIterator[FastAPI]:
    """The whole app against the test database, with an upstream that records
    requests and by default answers 200.

    Requests commit for real, each in its own session as in production, so
    this builds on committed_sessionmaker (which also empties the tables around
    each test). Tests can use that fixture to inspect or adjust the database.
    """

    def record(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return upstream.handler(request)

    app = create_app(
        settings=Settings(
            database_url=test_database_url, upstream_base_url="http://upstream.test"
        ),
        transport=httpx.MockTransport(record),
    )
    async with app.router.lifespan_context(app):
        # Serve from the shared engine rather than the one the lifespan made,
        # so requests reuse warm connections.
        app.state.db_engine = test_engine
        app.state.sessionmaker = committed_sessionmaker
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
