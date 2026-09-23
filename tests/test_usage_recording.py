"""Proxied requests are recorded, with their usage, against the test database."""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import Handler, Signup, Upstream
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from gate import dev_seed
from gate.models import ApiKey, ModelPrice, RequestLog, UsageRollup
from gate.usage import UsageRecorder, get_usage_recorder, hour_bucket

pytestmark = pytest.mark.anyio

URL = "/v1/chat/completions"
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]}
USAGE = {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500}
# At 500 and 1500 micros per 1k tokens: 1200 * 0.5 + 300 * 1.5 micros.
PRICE = {"input_micros_per_1k": 500, "output_micros_per_1k": 1500}
COST = 1050
# Nothing listens on port 1, so connecting fails at once.
UNREACHABLE_DATABASE = "postgresql+asyncpg://gate:gate@127.0.0.1:1/gate"

Sessions = async_sessionmaker[AsyncSession]


def completion(usage: object = USAGE) -> httpx.Response:
    return httpx.Response(200, json={"id": "chatcmpl-1", "usage": usage})


def sse(*payloads: object) -> httpx.Response:
    body = b"".join(f"data: {json.dumps(p)}\n\n".encode() for p in payloads)
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=body + b"data: [DONE]\n\n",
    )


class Proxy:
    """A signed-up owner with one API key, calling the proxy with it."""

    def __init__(self, client: httpx.AsyncClient, org_id: str) -> None:
        self.client = client
        self.org_id = uuid.UUID(org_id)
        self.key_id = uuid.UUID(int=0)
        self.key = ""

    async def create_key(self) -> None:
        response = await self.client.post(
            f"/api/v1/orgs/{self.org_id}/keys", json={"name": "ci"}
        )
        self.key_id, self.key = uuid.UUID(response.json()["id"]), response.json()["key"]

    async def revoke_key(self) -> None:
        url = f"/api/v1/orgs/{self.org_id}/keys/{self.key_id}"
        assert (await self.client.delete(url)).status_code == 204

    async def post(self, body: object = BODY) -> httpx.Response:
        return await self.client.post(
            URL, json=body, headers={"authorization": f"Bearer {self.key}"}
        )


@pytest.fixture
async def proxy(signup: Signup) -> Proxy:
    account = await signup("ada")
    proxy = Proxy(account.client, account.org_id)
    await proxy.create_key()
    return proxy


async def add_prices(sessions: Sessions, *prices: ModelPrice) -> None:
    async with sessions.begin() as session:
        session.add_all(prices)


async def requests_rows(sessions: Sessions) -> list[RequestLog]:
    async with sessions() as session:
        return list(await session.scalars(select(RequestLog)))


async def rollup_rows(sessions: Sessions) -> list[UsageRollup]:
    async with sessions() as session:
        return list(await session.scalars(select(UsageRollup)))


async def get_key(sessions: Sessions, key_id: uuid.UUID) -> ApiKey:
    async with sessions() as session:
        return await session.get_one(ApiKey, key_id)


def price(model: str, effective_from: datetime, input_per_1k: int) -> ModelPrice:
    return ModelPrice(
        model=model,
        effective_from=effective_from,
        input_micros_per_1k=input_per_1k,
        output_micros_per_1k=0,
    )


# --- successful requests ---


async def test_successful_request_records_a_row_and_a_rollup(
    proxy: Proxy, upstream: Upstream, committed_sessionmaker: Sessions
) -> None:
    upstream.handler = lambda _: completion()
    await add_prices(
        committed_sessionmaker,
        ModelPrice(
            model="mock-1", effective_from=datetime(2026, 1, 1, tzinfo=UTC), **PRICE
        ),
    )

    response = await proxy.post()

    assert response.status_code == 200
    assert response.json()["usage"] == USAGE
    [row] = await requests_rows(committed_sessionmaker)
    assert row.request_id.version == 7
    assert (row.org_id, row.api_key_id) == (proxy.org_id, proxy.key_id)
    assert (row.model, row.provider, row.status_code) == ("mock-1", "mock", 200)
    assert (row.prompt_tokens, row.completion_tokens) == (1200, 300)
    assert row.cost_micros == COST
    assert row.latency_ms >= 0
    assert row.streamed is False

    [rollup] = await rollup_rows(committed_sessionmaker)
    assert (rollup.api_key_id, rollup.org_id, rollup.model) == (
        proxy.key_id,
        proxy.org_id,
        "mock-1",
    )
    assert rollup.bucket_start == hour_bucket(row.created_at)
    assert (rollup.request_count, rollup.tokens, rollup.cost_micros) == (1, 1500, COST)

    key = await get_key(committed_sessionmaker, proxy.key_id)
    assert key.last_used_at == row.created_at


async def test_requests_in_the_same_hour_add_to_one_rollup(
    proxy: Proxy, upstream: Upstream, committed_sessionmaker: Sessions
) -> None:
    upstream.handler = lambda _: completion()

    await proxy.post()
    await proxy.post()

    assert len(await requests_rows(committed_sessionmaker)) == 2
    [rollup] = await rollup_rows(committed_sessionmaker)
    assert (rollup.request_count, rollup.tokens) == (2, 3000)


@pytest.mark.parametrize(
    ("events", "tokens"),
    [
        ([{"choices": []}, {"choices": [], "usage": USAGE}], (1200, 300)),
        ([{"choices": []}], (0, 0)),
    ],
    ids=["with-usage", "without-usage"],
)
async def test_streamed_request_takes_usage_from_the_stream(
    proxy: Proxy,
    upstream: Upstream,
    committed_sessionmaker: Sessions,
    events: list[object],
    tokens: tuple[int, int],
) -> None:
    upstream.handler = lambda _: sse(*events)

    response = await proxy.post({**BODY, "stream": True})

    assert response.status_code == 200
    [row] = await requests_rows(committed_sessionmaker)
    assert (row.status_code, row.streamed) == (200, True)
    assert (row.prompt_tokens, row.completion_tokens) == tokens
    [rollup] = await rollup_rows(committed_sessionmaker)
    assert (rollup.request_count, rollup.tokens) == (1, sum(tokens))


async def test_cost_uses_the_latest_price_already_in_effect(
    proxy: Proxy, upstream: Upstream, committed_sessionmaker: Sessions
) -> None:
    upstream.handler = lambda _: completion()
    now = datetime.now(UTC)
    await add_prices(
        committed_sessionmaker,
        price("mock-1", now - timedelta(days=30), input_per_1k=1000),
        price("mock-1", now - timedelta(days=1), input_per_1k=2000),
        price("mock-1", now + timedelta(days=1), input_per_1k=4000),
        price("mock-2", now - timedelta(days=1), input_per_1k=8000),
    )

    await proxy.post()

    [row] = await requests_rows(committed_sessionmaker)
    assert row.cost_micros == 1200 * 2


async def test_model_without_a_price_costs_nothing(
    proxy: Proxy, upstream: Upstream, committed_sessionmaker: Sessions
) -> None:
    upstream.handler = lambda _: completion()

    await proxy.post({**BODY, "model": "unpriced"})

    [row] = await requests_rows(committed_sessionmaker)
    assert (row.model, row.prompt_tokens, row.cost_micros) == ("unpriced", 1200, 0)


async def test_duplicate_request_id_is_a_no_op(
    proxy: Proxy, committed_sessionmaker: Sessions
) -> None:
    key = await get_key(committed_sessionmaker, proxy.key_id)
    recorder = UsageRecorder(committed_sessionmaker, "mock")

    for _ in range(2):
        await recorder.record(
            key=key, model="mock-1", status_code=200, usage=USAGE, streamed=False
        )

    [row] = await requests_rows(committed_sessionmaker)
    assert row.request_id == recorder.request_id
    [rollup] = await rollup_rows(committed_sessionmaker)
    assert (rollup.request_count, rollup.tokens) == (1, 1500)


# --- failed requests ---


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def time_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


def rate_limit(_: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"error": {"message": "slow down"}})


def bad_request_with_usage(_: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": {"message": "no"}, "usage": USAGE})


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "stream"])
@pytest.mark.parametrize(
    ("handler", "status_code", "tokens"),
    [
        (refuse, 502, (0, 0)),
        (time_out, 504, (0, 0)),
        (rate_limit, 429, (0, 0)),
        (bad_request_with_usage, 400, (1200, 300)),
    ],
    ids=["unreachable", "timeout", "upstream-429", "upstream-400-with-usage"],
)
async def test_failed_upstream_request_is_recorded(
    proxy: Proxy,
    upstream: Upstream,
    committed_sessionmaker: Sessions,
    handler: Handler,
    status_code: int,
    tokens: tuple[int, int],
    stream: bool,
) -> None:
    upstream.handler = handler

    response = await proxy.post({**BODY, "stream": stream})

    assert response.status_code == status_code
    [row] = await requests_rows(committed_sessionmaker)
    assert (row.status_code, row.streamed) == (status_code, False)
    assert (row.prompt_tokens, row.completion_tokens) == tokens
    [rollup] = await rollup_rows(committed_sessionmaker)
    assert rollup.request_count == 1


async def test_revoked_key_rejection_is_recorded(
    proxy: Proxy, committed_sessionmaker: Sessions, upstream_requests: list[object]
) -> None:
    await proxy.revoke_key()

    response = await proxy.post()

    assert response.status_code == 401
    assert upstream_requests == []
    [row] = await requests_rows(committed_sessionmaker)
    assert (row.api_key_id, row.model, row.status_code) == (
        proxy.key_id,
        "mock-1",
        401,
    )
    # A rejected attempt isn't a use of the key.
    assert (await get_key(committed_sessionmaker, proxy.key_id)).last_used_at is None


async def test_unknown_key_rejection_is_not_recorded(
    proxy: Proxy, committed_sessionmaker: Sessions
) -> None:
    # There's no key (and so no org) to attribute it to.
    proxy.key = "gk_unknown"

    assert (await proxy.post()).status_code == 401
    assert await requests_rows(committed_sessionmaker) == []


# --- recording failures ---


@pytest.fixture
async def broken_recorder(api_app: FastAPI) -> AsyncIterator[None]:
    """Recording writes to a database that refuses connections; key lookups
    still use the working one."""
    engine = create_async_engine(UNREACHABLE_DATABASE, poolclass=NullPool)
    sessions = async_sessionmaker(engine)
    api_app.dependency_overrides[get_usage_recorder] = lambda: UsageRecorder(
        sessions, "mock"
    )
    yield
    await engine.dispose()


@pytest.mark.usefixtures("broken_recorder")
@pytest.mark.parametrize("stream", [False, True], ids=["plain", "stream"])
async def test_database_failure_while_recording_does_not_break_the_response(
    proxy: Proxy,
    upstream: Upstream,
    committed_sessionmaker: Sessions,
    caplog: pytest.LogCaptureFixture,
    stream: bool,
) -> None:
    upstream.handler = lambda _: (
        sse({"choices": [], "usage": USAGE}) if stream else completion()
    )

    with caplog.at_level(logging.ERROR, logger="gate.usage"):
        response = await proxy.post({**BODY, "stream": stream})

    assert response.status_code == 200
    if stream:
        assert response.text.endswith("data: [DONE]\n\n")
    else:
        assert response.json()["usage"] == USAGE
    assert "Failed to record request" in caplog.text
    assert await requests_rows(committed_sessionmaker) == []


# --- development seed ---


async def test_dev_seed_prices_the_mock_models_and_can_rerun(
    test_database_url: str, committed_sessionmaker: Sessions
) -> None:
    await dev_seed.seed(test_database_url)
    await dev_seed.seed(test_database_url)

    async with committed_sessionmaker() as session:
        models = await session.scalars(select(ModelPrice.model))
        assert sorted(models) == sorted(model for model, *_ in dev_seed.MOCK_PRICES)
