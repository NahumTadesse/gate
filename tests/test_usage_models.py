import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert

from gate.db import Base
from gate.models import ApiKey, Organization, RequestLog, UsageRollup

pytestmark = pytest.mark.anyio

BUCKET = datetime(2026, 9, 22, 18, tzinfo=UTC)


async def make_key(db_session: AsyncSession) -> ApiKey:
    key = ApiKey(
        organization=Organization(name="Acme"),
        name="default",
        prefix="gk_abcde",
        key_hash=f"hash-{uuid.uuid4()}",
    )
    db_session.add(key)
    await db_session.flush()
    return key


def request_row(
    key: ApiKey, request_id: uuid.UUID, **overrides: object
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "org_id": key.org_id,
        "api_key_id": key.id,
        "model": "gpt-test",
        "provider": "mock",
        "status_code": 200,
        "latency_ms": 12,
        **overrides,
    }


async def test_defaults_are_applied(db_session: AsyncSession) -> None:
    key = await make_key(db_session)
    request = RequestLog(**request_row(key, uuid.uuid4()))
    db_session.add(request)
    await db_session.flush()

    assert key.rpm_limit == 60
    assert key.monthly_budget_micros is None
    assert (request.prompt_tokens, request.completion_tokens) == (0, 0)
    assert request.cost_micros == 0
    assert request.streamed is False


@pytest.mark.parametrize(
    ("table", "column", "constraint"),
    [
        ("api_keys", "org_id", "fk_api_keys_org_id_organizations"),
        ("requests", "org_id", "fk_requests_api_key_id_org_id_api_keys"),
        ("requests", "api_key_id", "fk_requests_api_key_id_org_id_api_keys"),
        ("usage_rollups", "org_id", "fk_usage_rollups_api_key_id_org_id_api_keys"),
        ("usage_rollups", "api_key_id", "fk_usage_rollups_api_key_id_org_id_api_keys"),
    ],
)
async def test_foreign_keys_are_enforced(
    db_session: AsyncSession, table: str, column: str, constraint: str
) -> None:
    key = await make_key(db_session)
    missing = uuid.uuid4()
    rows = {
        "api_keys": {
            "id": uuid.uuid4(),
            "org_id": missing,
            "name": "k",
            "prefix": "gk_12345",
            "key_hash": "h",
        },
        "requests": {"id": uuid.uuid4(), **request_row(key, uuid.uuid4())},
        "usage_rollups": {
            "api_key_id": key.id,
            "model": "gpt-test",
            "bucket_start": BUCKET,
            "org_id": key.org_id,
        },
    }
    row = rows[table]
    row[column] = missing

    with pytest.raises(IntegrityError, match=constraint):
        await db_session.execute(insert(Base.metadata.tables[table]).values(row))


async def test_request_with_another_orgs_key_is_rejected(
    db_session: AsyncSession,
) -> None:
    key, other_key = await make_key(db_session), await make_key(db_session)
    assert key.org_id != other_key.org_id

    # Both ids exist, so separate FKs on each column would accept this row.
    db_session.add(
        RequestLog(**request_row(key, uuid.uuid4(), org_id=other_key.org_id))
    )
    with pytest.raises(IntegrityError, match="fk_requests_api_key_id_org_id_api_keys"):
        await db_session.flush()


async def test_rollup_with_another_orgs_key_is_rejected(
    db_session: AsyncSession,
) -> None:
    key, other_key = await make_key(db_session), await make_key(db_session)
    assert key.org_id != other_key.org_id

    # Both ids exist, so separate FKs on each column would accept this row.
    db_session.add(
        UsageRollup(
            api_key_id=key.id,
            model="gpt-test",
            bucket_start=BUCKET,
            org_id=other_key.org_id,
        )
    )
    with pytest.raises(
        IntegrityError, match="fk_usage_rollups_api_key_id_org_id_api_keys"
    ):
        await db_session.flush()


async def test_request_id_is_unique(db_session: AsyncSession) -> None:
    key = await make_key(db_session)
    request_id = uuid.uuid4()
    db_session.add(RequestLog(**request_row(key, request_id)))
    await db_session.flush()

    db_session.add(RequestLog(**request_row(key, request_id)))
    with pytest.raises(IntegrityError, match="uq_requests_request_id"):
        await db_session.flush()


async def test_duplicate_request_id_on_conflict_do_nothing_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    key = await make_key(db_session)
    request_id = uuid.uuid4()

    def statement(latency_ms: int) -> ReturningInsert[tuple[uuid.UUID]]:
        return (
            insert(RequestLog)
            .values(request_row(key, request_id, latency_ms=latency_ms))
            .on_conflict_do_nothing(index_elements=[RequestLog.request_id])
            .returning(RequestLog.id)
        )

    assert await db_session.scalar(statement(12)) is not None
    assert await db_session.scalar(statement(99)) is None

    rows = (await db_session.scalars(select(RequestLog.latency_ms))).all()
    assert rows == [12]


async def test_rollup_upsert_increments(db_session: AsyncSession) -> None:
    key = await make_key(db_session)

    async def record(requests: int, tokens: int, cost_micros: int) -> None:
        statement = insert(UsageRollup).values(
            api_key_id=key.id,
            model="gpt-test",
            bucket_start=BUCKET,
            org_id=key.org_id,
            request_count=requests,
            tokens=tokens,
            cost_micros=cost_micros,
        )
        await db_session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    UsageRollup.api_key_id,
                    UsageRollup.model,
                    UsageRollup.bucket_start,
                ],
                set_={
                    "request_count": UsageRollup.request_count
                    + statement.excluded.request_count,
                    "tokens": UsageRollup.tokens + statement.excluded.tokens,
                    "cost_micros": UsageRollup.cost_micros
                    + statement.excluded.cost_micros,
                },
            )
        )

    await record(1, 100, 250)
    await record(1, 40, 90)
    await record(3, 10, 5)

    rollup = (
        await db_session.execute(
            select(
                func.count(),
                func.sum(UsageRollup.request_count),
                func.sum(UsageRollup.tokens),
                func.sum(UsageRollup.cost_micros),
            )
        )
    ).one()
    assert tuple(rollup) == (1, 5, 150, 345)


async def test_rollup_bucket_must_be_on_the_hour(db_session: AsyncSession) -> None:
    key = await make_key(db_session)
    db_session.add(
        UsageRollup(
            api_key_id=key.id,
            model="gpt-test",
            bucket_start=BUCKET + timedelta(minutes=30),
            org_id=key.org_id,
        )
    )
    with pytest.raises(IntegrityError, match="ck_usage_rollups_bucket_start_hour"):
        await db_session.flush()


async def test_rollup_bucket_check_ignores_session_time_zone(
    db_session: AsyncSession,
) -> None:
    # India is UTC+05:30, where date_trunc('hour', ...) would disagree with UTC.
    await db_session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
    key = await make_key(db_session)
    db_session.add(
        UsageRollup(
            api_key_id=key.id, model="gpt-test", bucket_start=BUCKET, org_id=key.org_id
        )
    )
    await db_session.flush()


async def test_indexes_exist(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes"
            " WHERE schemaname = current_schema()"
            " AND tablename IN ('api_keys', 'requests')"
            " AND indexname LIKE 'ix\\_%'"
        )
    )
    indexes = {name: definition for name, definition in rows}

    assert set(indexes) == {
        "ix_api_keys_org_id",
        "ix_requests_org_id_created_at",
        "ix_requests_api_key_id_created_at",
    }
    assert indexes["ix_api_keys_org_id"].endswith("(org_id)")
    assert indexes["ix_requests_org_id_created_at"].endswith(
        "(org_id, created_at DESC)"
    )
    assert indexes["ix_requests_api_key_id_created_at"].endswith(
        "(api_key_id, created_at DESC)"
    )
