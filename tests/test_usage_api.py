import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import Account, Signup
from fastapi import FastAPI
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from gate.models import ApiKey, UsageRollup

pytestmark = pytest.mark.anyio

Sessionmaker = async_sessionmaker[AsyncSession]

DAY1 = datetime(2026, 9, 1, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
RANGE = {"from": DAY1.isoformat(), "to": (DAY2 + timedelta(days=1)).isoformat()}


async def add_key(sessionmaker: Sessionmaker, account: Account) -> uuid.UUID:
    key = ApiKey(
        org_id=uuid.UUID(account.org_id),
        name="k",
        prefix="gk_xxxxx",
        key_hash=str(uuid.uuid4()),
    )
    async with sessionmaker() as session:
        session.add(key)
        await session.commit()
    return key.id


async def add_rollups(
    sessionmaker: Sessionmaker, account: Account, rows: list[dict[str, Any]]
) -> None:
    async with sessionmaker() as session:
        session.add_all(
            UsageRollup(org_id=uuid.UUID(account.org_id), **row) for row in rows
        )
        await session.commit()


@pytest.fixture
async def usage(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> tuple[Account, uuid.UUID, uuid.UUID]:
    """Two keys and two models across two UTC days:

    day 1 01:00  key_a gpt-a  1 req  10 tok  100 micros
    day 1 01:00  key_a gpt-b  2 req  20 tok  200 micros
    day 1 23:00  key_b gpt-a  4 req  40 tok  400 micros
    day 2 00:00  key_a gpt-a  8 req  80 tok  800 micros
    """
    ada = await signup("ada")
    key_a = await add_key(committed_sessionmaker, ada)
    key_b = await add_key(committed_sessionmaker, ada)

    def rollup(key: uuid.UUID, model: str, at: datetime, n: int) -> dict[str, Any]:
        return {
            "api_key_id": key,
            "model": model,
            "bucket_start": at,
            "request_count": n,
            "tokens": n * 10,
            "cost_micros": n * 100,
        }

    await add_rollups(
        committed_sessionmaker,
        ada,
        [
            rollup(key_a, "gpt-a", DAY1 + timedelta(hours=1), 1),
            rollup(key_a, "gpt-b", DAY1 + timedelta(hours=1), 2),
            rollup(key_b, "gpt-a", DAY1 + timedelta(hours=23), 4),
            rollup(key_a, "gpt-a", DAY2, 8),
        ],
    )
    return ada, key_a, key_b


async def get_usage(account: Account, **params: str) -> dict[str, Any]:
    response = await account.client.get(account.org("/usage"), params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def totals(row: dict[str, Any]) -> tuple[int, int, int]:
    return row["request_count"], row["tokens"], row["cost_micros"]


def at(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


async def test_hourly_totals(usage: tuple[Account, uuid.UUID, uuid.UUID]) -> None:
    ada, _, _ = usage

    body = await get_usage(ada, **RANGE)

    assert (body["bucket"], body["group_by"]) == ("hour", None)
    assert [(row["bucket_start"], totals(row)) for row in body["data"]] == [
        (at(DAY1 + timedelta(hours=1)), (3, 30, 300)),
        (at(DAY1 + timedelta(hours=23)), (4, 40, 400)),
        (at(DAY2), (8, 80, 800)),
    ]
    assert all(row["model"] is row["api_key_id"] is None for row in body["data"])


@pytest.fixture
async def far_east_database(
    api_app: FastAPI, test_database_url: str
) -> AsyncIterator[None]:
    """Makes the app's database sessions default to UTC+14, where 23:00 UTC is
    already the next day, for the length of a test."""
    name = make_url(test_database_url).database
    engine = create_async_engine(test_database_url, poolclass=NullPool)

    async def set_time_zone(value: str) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(f'ALTER DATABASE "{name}" SET timezone TO {value}')
            )
        # Pooled connections keep the old setting; new ones pick this up.
        await api_app.state.db_engine.dispose()

    await set_time_zone("'Pacific/Kiritimati'")
    try:
        yield
    finally:
        await set_time_zone("DEFAULT")
        await engine.dispose()


async def test_daily_totals_use_utc_days(
    usage: tuple[Account, uuid.UUID, uuid.UUID], far_east_database: None
) -> None:
    ada, _, _ = usage

    body = await get_usage(ada, **RANGE, bucket="day")

    # 23:00 UTC belongs to day 1 even though the session's zone says day 2.
    assert [(row["bucket_start"], totals(row)) for row in body["data"]] == [
        (at(DAY1), (7, 70, 700)),
        (at(DAY2), (8, 80, 800)),
    ]


async def test_group_by_model(usage: tuple[Account, uuid.UUID, uuid.UUID]) -> None:
    ada, _, _ = usage

    body = await get_usage(ada, **RANGE, bucket="day", group_by="model")

    assert [
        (row["bucket_start"], row["model"], totals(row)) for row in body["data"]
    ] == [
        (at(DAY1), "gpt-a", (5, 50, 500)),
        (at(DAY1), "gpt-b", (2, 20, 200)),
        (at(DAY2), "gpt-a", (8, 80, 800)),
    ]
    assert all(row["api_key_id"] is None for row in body["data"])


async def test_group_by_key(usage: tuple[Account, uuid.UUID, uuid.UUID]) -> None:
    ada, key_a, key_b = usage

    body = await get_usage(ada, **RANGE, bucket="day", group_by="key")

    got = {
        (row["bucket_start"], row["api_key_id"]): totals(row) for row in body["data"]
    }
    assert got == {
        (at(DAY1), str(key_a)): (3, 30, 300),
        (at(DAY1), str(key_b)): (4, 40, 400),
        (at(DAY2), str(key_a)): (8, 80, 800),
    }
    assert all(row["model"] is None for row in body["data"])


async def test_range_is_inclusive_start_exclusive_end(
    usage: tuple[Account, uuid.UUID, uuid.UUID],
) -> None:
    ada, _, _ = usage

    body = await get_usage(
        ada,
        **{
            "from": (DAY1 + timedelta(hours=1)).isoformat(),
            "to": (DAY1 + timedelta(hours=23)).isoformat(),
        },
    )

    assert [totals(row) for row in body["data"]] == [(3, 30, 300)]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (DAY1 + timedelta(hours=2), DAY1 + timedelta(hours=3)),  # between buckets
        (DAY1, DAY1),  # zero width
        (DAY2, DAY1),  # inverted
    ],
    ids=["gap", "zero-width", "inverted"],
)
async def test_empty_range_is_an_empty_report(
    usage: tuple[Account, uuid.UUID, uuid.UUID], start: datetime, end: datetime
) -> None:
    ada, _, _ = usage

    body = await get_usage(ada, **{"from": start.isoformat(), "to": end.isoformat()})

    assert body["data"] == []


async def test_other_orgs_usage_is_excluded(
    signup: Signup,
    committed_sessionmaker: Sessionmaker,
    usage: tuple[Account, uuid.UUID, uuid.UUID],
) -> None:
    ada, _, _ = usage
    eve = await signup("eve")
    eve_key = await add_key(committed_sessionmaker, eve)
    await add_rollups(
        committed_sessionmaker,
        eve,
        [{"api_key_id": eve_key, "model": "gpt-a", "bucket_start": DAY1, "tokens": 9}],
    )

    body = await get_usage(ada, **RANGE, bucket="day")

    assert [totals(row) for row in body["data"]] == [(7, 70, 700), (8, 80, 800)]
    assert (await eve.client.get(ada.org("/usage"), params=RANGE)).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"to": RANGE["to"]},
        {"from": RANGE["from"]},
        {**RANGE, "bucket": "week"},
        {**RANGE, "group_by": "provider"},
        {"from": "2026-09-01T00:00:00", "to": RANGE["to"]},
    ],
    ids=["no-from", "no-to", "bad-bucket", "bad-group", "naive-datetime"],
)
async def test_malformed_params_are_rejected(
    signup: Signup, params: dict[str, str]
) -> None:
    ada = await signup("ada")

    response = await ada.client.get(ada.org("/usage"), params=params)

    assert response.status_code == 422
