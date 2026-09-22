import base64
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from conftest import Account, Signup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from gate.models import ApiKey, RequestLog

pytestmark = pytest.mark.anyio

Sessionmaker = async_sessionmaker[AsyncSession]

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)


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


async def add_requests(
    sessionmaker: Sessionmaker,
    account: Account,
    key_id: uuid.UUID,
    rows: Sequence[dict[str, Any]],
) -> list[str]:
    """Inserts requests; returns their ids, in the order the API should list
    them (newest first, ties broken by id descending)."""
    records = [
        RequestLog(
            id=row.get("id", uuid7()),
            request_id=uuid.uuid4(),
            org_id=uuid.UUID(account.org_id),
            api_key_id=row.get("api_key_id", key_id),
            model=row.get("model", "gpt-a"),
            provider="mock",
            status_code=row.get("status_code", 200),
            latency_ms=10,
            created_at=row["created_at"],
        )
        for row in rows
    ]
    async with sessionmaker() as session:
        session.add_all(records)
        await session.commit()
    ordered = sorted(records, key=lambda r: (r.created_at, r.id), reverse=True)
    return [str(r.id) for r in ordered]


async def list_page(account: Account, **params: Any) -> httpx.Response:
    return await account.client.get(
        account.org("/requests"),
        params={k: v for k, v in params.items() if v is not None},
    )


async def walk(account: Account, **params: Any) -> list[list[str]]:
    """Every page's ids, following next_cursor to the end."""
    pages: list[list[str]] = []
    cursor = None
    while True:
        response = await list_page(account, cursor=cursor, **params)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append([row["id"] for row in body["data"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


# --- pagination ---


async def test_pages_cover_every_row_once_in_order(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> None:
    ada = await signup("ada")
    key = await add_key(committed_sessionmaker, ada)
    # Five rows share one timestamp, so pages have to split inside a tie.
    rows = [{"created_at": T0 + timedelta(minutes=i)} for i in range(4)]
    rows += [{"created_at": T0 + timedelta(minutes=10)} for _ in range(5)]
    expected = await add_requests(committed_sessionmaker, ada, key, rows)

    pages = await walk(ada, limit=2)

    assert [row for page in pages for row in page] == expected
    assert [len(page) for page in pages] == [2, 2, 2, 2, 1]


async def test_exact_multiple_of_limit_has_no_empty_last_page(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> None:
    ada = await signup("ada")
    key = await add_key(committed_sessionmaker, ada)
    rows = [{"created_at": T0 + timedelta(minutes=i)} for i in range(4)]
    expected = await add_requests(committed_sessionmaker, ada, key, rows)

    pages = await walk(ada, limit=2)

    assert pages == [expected[:2], expected[2:]]


async def test_rows_inserted_mid_walk_do_not_shift_pages(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> None:
    ada = await signup("ada")
    key = await add_key(committed_sessionmaker, ada)
    rows = [{"created_at": T0 + timedelta(minutes=i)} for i in range(6)]
    expected = await add_requests(committed_sessionmaker, ada, key, rows)

    first = (await list_page(ada, limit=3)).json()
    assert [row["id"] for row in first["data"]] == expected[:3]

    # New traffic arrives, including rows tied with the page boundary that
    # sort before it. With offset pagination these would push already-seen
    # rows onto the next page.
    boundary = T0 + timedelta(minutes=3)
    await add_requests(
        committed_sessionmaker,
        ada,
        key,
        [{"created_at": datetime.now(UTC)} for _ in range(3)]
        + [{"created_at": boundary, "id": uuid.UUID(int=2**128 - 1)}],
    )

    second = (await list_page(ada, limit=3, cursor=first["next_cursor"])).json()
    assert [row["id"] for row in second["data"]] == expected[3:]
    assert second["next_cursor"] is None


# --- filters ---


@pytest.fixture
async def mixed(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> tuple[Account, dict[str, dict[str, Any]], uuid.UUID]:
    """ada's org with one request per distinguishing trait, keyed by name."""
    ada = await signup("ada")
    key_a = await add_key(committed_sessionmaker, ada)
    key_b = await add_key(committed_sessionmaker, ada)
    rows: dict[str, dict[str, Any]] = {
        "base": {"created_at": T0},
        "model_b": {"created_at": T0 + timedelta(hours=1), "model": "gpt-b"},
        "error": {"created_at": T0 + timedelta(hours=2), "status_code": 429},
        "key_b": {"created_at": T0 + timedelta(hours=3), "api_key_id": key_b},
        "model_b_error": {
            "created_at": T0 + timedelta(hours=4),
            "model": "gpt-b",
            "status_code": 429,
        },
    }
    for row in rows.values():
        [row["id_str"]] = await add_requests(committed_sessionmaker, ada, key_a, [row])
    return ada, rows, key_b


def names(rows: dict[str, dict[str, Any]], ids: list[str]) -> set[str]:
    by_id = {row["id_str"]: name for name, row in rows.items()}
    return {by_id[i] for i in ids}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"model": "gpt-b"}, {"model_b", "model_b_error"}),
        ({"status_code": 429}, {"error", "model_b_error"}),
        ({"api_key_id": "KEY_B"}, {"key_b"}),
        ({"from": (T0 + timedelta(hours=3)).isoformat()}, {"key_b", "model_b_error"}),
        ({"to": (T0 + timedelta(hours=1)).isoformat()}, {"base"}),
        (
            {
                "from": (T0 + timedelta(hours=1)).isoformat(),
                "to": (T0 + timedelta(hours=3)).isoformat(),
            },
            {"model_b", "error"},
        ),
        ({"model": "gpt-b", "status_code": 429}, {"model_b_error"}),
        ({"model": "gpt-a", "status_code": 429}, {"error"}),
        (
            {"status_code": 429, "to": (T0 + timedelta(hours=4)).isoformat()},
            {"error"},
        ),
        ({"model": "gpt-a", "api_key_id": "KEY_B", "status_code": 200}, {"key_b"}),
    ],
    ids=[
        "model",
        "status",
        "key",
        "from-inclusive",
        "to-exclusive",
        "range",
        "model+status",
        "other-model+status",
        "status+to",
        "model+key+status",
    ],
)
async def test_filters(
    mixed: tuple[Account, dict[str, dict[str, Any]], uuid.UUID],
    params: dict[str, Any],
    expected: set[str],
) -> None:
    ada, rows, key_b = mixed
    params = {k: str(key_b) if v == "KEY_B" else v for k, v in params.items()}

    [page] = await walk(ada, **params)

    assert names(rows, page) == expected


async def test_filters_hold_across_pages(
    mixed: tuple[Account, dict[str, dict[str, Any]], uuid.UUID],
) -> None:
    ada, rows, _ = mixed

    pages = await walk(ada, model="gpt-b", limit=1)

    assert [names(rows, page) for page in pages] == [{"model_b_error"}, {"model_b"}]


@pytest.mark.parametrize(
    "params",
    [
        {"from": T0.isoformat(), "to": T0.isoformat()},
        {"from": (T0 + timedelta(days=1)).isoformat()},
        {"to": (T0 - timedelta(days=1)).isoformat()},
        {"from": (T0 + timedelta(hours=2)).isoformat(), "to": T0.isoformat()},
        {"model": "no-such-model"},
    ],
    ids=["zero-width", "after-all", "before-all", "inverted", "no-match"],
)
async def test_empty_results_are_an_empty_page(
    mixed: tuple[Account, dict[str, dict[str, Any]], uuid.UUID],
    params: dict[str, Any],
) -> None:
    ada, _, _ = mixed

    response = await list_page(ada, **params)

    assert response.status_code == 200
    assert response.json() == {"data": [], "next_cursor": None}


async def test_other_orgs_requests_are_not_listed(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> None:
    ada, eve = await signup("ada"), await signup("eve")
    await add_requests(
        committed_sessionmaker,
        eve,
        await add_key(committed_sessionmaker, eve),
        [{"created_at": T0}],
    )

    assert (await list_page(ada)).json()["data"] == []
    assert (await eve.client.get(ada.org("/requests"))).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-09-01T12:00:00"},  # no time zone: ambiguous
        {"to": "yesterday"},
        {"status_code": 42},
        {"status_code": 70000},
        {"api_key_id": "not-a-uuid"},
        {"limit": 0},
        {"limit": 201},
    ],
)
async def test_malformed_params_are_rejected(
    signup: Signup, params: dict[str, Any]
) -> None:
    ada = await signup("ada")

    response = await list_page(ada, **params)

    assert response.status_code == 422


# --- cursor tampering ---


def reencode_payload(cursor: str, **changes: str) -> str:
    """Edit the payload but keep the original signature."""
    payload, signature = cursor.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data.update(changes)
    edited = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")
    return f"{edited.decode()}.{signature}"


@pytest.fixture
async def cursor(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> tuple[Account, str]:
    ada = await signup("ada")
    key = await add_key(committed_sessionmaker, ada)
    rows = [{"created_at": T0 + timedelta(minutes=i)} for i in range(3)]
    await add_requests(committed_sessionmaker, ada, key, rows)
    body = (await list_page(ada, model="gpt-a", limit=1)).json()
    return ada, body["next_cursor"]


@pytest.mark.parametrize(
    "tamper",
    [
        lambda c: reencode_payload(c, t="2030-01-01T00:00:00+00:00"),
        lambda c: reencode_payload(c, i=str(uuid.UUID(int=0))),
        lambda c: c.split(".")[0] + ".AAAA",
        lambda c: c[:-2],
        lambda c: c.replace(".", ""),
        lambda c: "not a cursor",
        lambda c: "",
        lambda c: "!!!.!!!",
    ],
    ids=[
        "edited-time",
        "edited-id",
        "forged-signature",
        "truncated",
        "no-separator",
        "garbage",
        "empty",
        "bad-base64",
    ],
)
async def test_tampered_cursor_is_rejected(
    cursor: tuple[Account, str], tamper: Any
) -> None:
    ada, token = cursor

    response = await list_page(ada, model="gpt-a", limit=1, cursor=tamper(token))

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid cursor"}


@pytest.mark.parametrize(
    "params",
    [
        {"model": "gpt-b"},
        {},  # dropping a filter changes the query too
        {"model": "gpt-a", "status_code": 200},
        {"model": "gpt-a", "from": T0.isoformat()},
    ],
)
async def test_cursor_is_bound_to_its_filters(
    cursor: tuple[Account, str], params: dict[str, Any]
) -> None:
    ada, token = cursor

    response = await list_page(ada, cursor=token, **params)

    assert response.status_code == 400


async def test_cursor_allows_a_different_page_size(
    cursor: tuple[Account, str],
) -> None:
    ada, token = cursor

    response = await list_page(ada, model="gpt-a", limit=5, cursor=token)

    assert response.status_code == 200
    assert len(response.json()["data"]) == 2
