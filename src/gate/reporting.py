"""Read-only reporting on an org's traffic: the request log and usage totals."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, DateTime, cast, func, or_, select

from gate.api import IN_ORG, error
from gate.auth import DbSession, OrgMember, get_settings
from gate.config import Settings
from gate.models import RequestLog, UsageRollup
from gate.pagination import (
    Cursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    query_fingerprint,
)

router = APIRouter(prefix="/api/v1", tags=["management"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


# `from` is a Python keyword, hence the alias. Factories so each parameter
# gets its own Query object.
def from_query() -> Any:
    return Query(alias="from", description="Start of the range, inclusive.")


def to_query() -> Any:
    return Query(description="End of the range, exclusive.")


def utc(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(UTC).isoformat()


# --- the request log ---


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: uuid.UUID
    api_key_id: uuid.UUID
    model: str
    provider: str
    status_code: int
    prompt_tokens: int
    completion_tokens: int
    cost_micros: int
    latency_ms: int
    streamed: bool
    created_at: datetime


class RequestPage(BaseModel):
    data: list[RequestOut] = Field(description="Newest first.")
    next_cursor: str | None = Field(
        description="Pass as `cursor`, with the same filters, for the next page. "
        "Null on the last page."
    )


@router.get(
    "/orgs/{org_id}/requests",
    responses={
        **IN_ORG,
        400: error("The cursor was altered, or issued for other filters"),
    },
)
async def list_requests(
    membership: OrgMember,
    session: DbSession,
    settings: SettingsDep,
    model: str | None = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    api_key_id: uuid.UUID | None = None,
    from_: Annotated[AwareDatetime | None, from_query()] = None,
    to: Annotated[AwareDatetime | None, to_query()] = None,
    cursor: Annotated[
        str | None, Query(description="`next_cursor` from the previous page.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RequestPage:
    """The org's requests, newest first, one page at a time."""
    secret = settings.secret_key.get_secret_value().encode()
    # Everything that selects rows, but not limit: page size may change freely.
    fingerprint = query_fingerprint(
        membership.org_id, model, status_code, api_key_id, utc(from_), utc(to)
    )

    query = select(RequestLog).where(RequestLog.org_id == membership.org_id)
    if model is not None:
        query = query.where(RequestLog.model == model)
    if status_code is not None:
        query = query.where(RequestLog.status_code == status_code)
    if api_key_id is not None:
        query = query.where(RequestLog.api_key_id == api_key_id)
    if from_ is not None:
        query = query.where(RequestLog.created_at >= from_)
    if to is not None:
        query = query.where(RequestLog.created_at < to)
    if cursor is not None:
        try:
            after = decode_cursor(cursor, fingerprint, secret)
        except InvalidCursorError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid cursor") from None
        # Equivalent to (created_at, id) < (after.created_at, after.id), but
        # written so the created_at bound can seek in the (org_id, created_at)
        # index; the row comparison alone isn't usable by that index.
        query = query.where(
            RequestLog.created_at <= after.created_at,
            or_(
                RequestLog.created_at < after.created_at,
                RequestLog.id < after.id,
            ),
        )

    # One extra row says whether another page exists, without a count query.
    rows = (
        await session.scalars(
            query.order_by(RequestLog.created_at.desc(), RequestLog.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = encode_cursor(
            Cursor(last.created_at, last.id), fingerprint, secret
        )
    return RequestPage(
        data=[RequestOut.model_validate(row) for row in page], next_cursor=next_cursor
    )


# --- usage ---


class UsageRow(BaseModel):
    bucket_start: datetime
    model: str | None = Field(default=None, description="Set when grouped by model.")
    api_key_id: uuid.UUID | None = Field(
        default=None, description="Set when grouped by key."
    )
    request_count: int
    tokens: int
    cost_micros: int


class UsageReport(BaseModel):
    bucket: Literal["hour", "day"]
    group_by: Literal["model", "key"] | None
    data: list[UsageRow] = Field(description="Ordered by bucket, then group.")


@router.get("/orgs/{org_id}/usage", responses=IN_ORG)
async def get_usage(
    membership: OrgMember,
    session: DbSession,
    from_: Annotated[AwareDatetime, from_query()],
    to: Annotated[AwareDatetime, to_query()],
    bucket: Literal["hour", "day"] = "hour",
    group_by: Literal["model", "key"] | None = None,
) -> UsageReport:
    """Usage totals from the hourly rollups.

    A rollup counts toward the range when its hour starts inside it, and day
    buckets are UTC days. Buckets and groups with no usage are left out.
    """
    bucket_start = (
        UsageRollup.bucket_start
        if bucket == "hour"
        # Explicitly UTC: the two-argument date_trunc would use the session's
        # time zone.
        else func.date_trunc(
            "day", UsageRollup.bucket_start, "UTC", type_=DateTime(timezone=True)
        )
    ).label("bucket_start")
    group = {"model": UsageRollup.model, "key": UsageRollup.api_key_id}.get(
        group_by or ""
    )
    keys = [bucket_start] if group is None else [bucket_start, group]

    rows = await session.execute(
        select(
            *keys,
            cast(func.sum(UsageRollup.request_count), BigInteger).label(
                "request_count"
            ),
            cast(func.sum(UsageRollup.tokens), BigInteger).label("tokens"),
            cast(func.sum(UsageRollup.cost_micros), BigInteger).label("cost_micros"),
        )
        .where(
            UsageRollup.org_id == membership.org_id,
            UsageRollup.bucket_start >= from_,
            UsageRollup.bucket_start < to,
        )
        .group_by(*keys)
        .order_by(*keys)
    )
    return UsageReport(
        bucket=bucket,
        group_by=group_by,
        data=[
            UsageRow(
                bucket_start=row.bucket_start,
                model=row.model if group_by == "model" else None,
                api_key_id=row.api_key_id if group_by == "key" else None,
                request_count=row.request_count,
                tokens=row.tokens,
                cost_micros=row.cost_micros,
            )
            for row in rows
        ],
    )
