"""Recording each proxied request, and its usage, as it finishes.

Recording is synchronous (the row is written before the request is done) but
best effort: a failed write is logged and never fails the proxied request.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from gate.models import ApiKey, ModelPrice, RequestLog, UsageRollup

logger = logging.getLogger(__name__)

# Bounds how long a slow database can hold up a response that is otherwise done.
RECORD_TIMEOUT = 5.0


@dataclass(frozen=True)
class TokenCounts:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @classmethod
    def from_upstream(cls, usage: object) -> "TokenCounts":
        """The counts from an upstream usage object; zero for anything missing
        or malformed. Gate never estimates tokens itself."""
        if not isinstance(usage, dict):
            return cls()
        return cls(
            prompt_tokens=token_count(usage.get("prompt_tokens")),
            completion_tokens=token_count(usage.get("completion_tokens")),
        )

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def token_count(value: object) -> int:
    # bool is an int subclass, but true isn't a token count.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


@dataclass(frozen=True)
class RequestRow:
    request_id: uuid.UUID
    org_id: uuid.UUID
    api_key_id: uuid.UUID
    model: str
    provider: str
    status_code: int
    tokens: TokenCounts
    latency_ms: int
    streamed: bool
    created_at: datetime


def hour_bucket(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def cost_micros(tokens: TokenCounts, input_per_1k: int, output_per_1k: int) -> int:
    # Rounded half up to a whole micro.
    thousandths = tokens.prompt_tokens * input_per_1k
    thousandths += tokens.completion_tokens * output_per_1k
    return (thousandths + 500) // 1000


async def price_request(session: AsyncSession, row: RequestRow) -> int:
    """The request's cost at the model's price in effect when it was made, or 0
    if the model has no price."""
    price = (
        await session.execute(
            select(ModelPrice.input_micros_per_1k, ModelPrice.output_micros_per_1k)
            .where(
                ModelPrice.model == row.model,
                ModelPrice.effective_from <= row.created_at,
            )
            .order_by(ModelPrice.effective_from.desc())
            .limit(1)
        )
    ).first()
    if price is None:
        return 0
    return cost_micros(row.tokens, *price)


async def save_request(session: AsyncSession, row: RequestRow) -> bool:
    """Insert the request and add it to its hourly rollup, in the session's
    transaction. Returns False, changing nothing, if request_id was already
    recorded."""
    cost = await price_request(session, row)
    inserted = await session.scalar(
        insert(RequestLog)
        .values(
            request_id=row.request_id,
            org_id=row.org_id,
            api_key_id=row.api_key_id,
            model=row.model,
            provider=row.provider,
            status_code=row.status_code,
            prompt_tokens=row.tokens.prompt_tokens,
            completion_tokens=row.tokens.completion_tokens,
            cost_micros=cost,
            latency_ms=row.latency_ms,
            streamed=row.streamed,
            created_at=row.created_at,
        )
        .on_conflict_do_nothing(index_elements=[RequestLog.request_id])
        .returning(RequestLog.id)
    )
    if inserted is None:
        return False

    rollup = insert(UsageRollup).values(
        api_key_id=row.api_key_id,
        model=row.model,
        bucket_start=hour_bucket(row.created_at),
        org_id=row.org_id,
        request_count=1,
        tokens=row.tokens.total,
        cost_micros=cost,
    )
    await session.execute(
        rollup.on_conflict_do_update(
            index_elements=[
                UsageRollup.api_key_id,
                UsageRollup.model,
                UsageRollup.bucket_start,
            ],
            set_={
                "request_count": UsageRollup.request_count + 1,
                "tokens": UsageRollup.tokens + rollup.excluded.tokens,
                "cost_micros": UsageRollup.cost_micros + rollup.excluded.cost_micros,
            },
        )
    )
    # A revoked key's rejected attempts aren't uses. GREATEST ignores NULL and
    # keeps a slower, earlier request from moving the time backwards.
    await session.execute(
        update(ApiKey)
        .where(ApiKey.id == row.api_key_id, ApiKey.revoked_at.is_(None))
        .values(last_used_at=func.greatest(ApiKey.last_used_at, row.created_at))
    )
    return True


class UsageRecorder:
    """Records one proxied request. Created per request, when it starts."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], provider: str
    ) -> None:
        self.sessionmaker = sessionmaker
        self.provider = provider
        self.request_id = uuid7()
        self.started_at = datetime.now(UTC)
        self._started = time.monotonic()

    async def record(
        self,
        *,
        key: ApiKey,
        model: str,
        status_code: int,
        usage: object,
        streamed: bool,
    ) -> None:
        """Write the request's row. Never raises: failures are logged."""
        row = RequestRow(
            request_id=self.request_id,
            org_id=key.org_id,
            api_key_id=key.id,
            model=model,
            provider=self.provider,
            status_code=status_code,
            tokens=TokenCounts.from_upstream(usage),
            latency_ms=round((time.monotonic() - self._started) * 1000),
            streamed=streamed,
            created_at=self.started_at,
        )
        try:
            with anyio.fail_after(RECORD_TIMEOUT):
                async with self.sessionmaker.begin() as session:
                    await save_request(session, row)
        except Exception:
            logger.exception("Failed to record request %s", row.request_id)


def get_usage_recorder(request: Request) -> UsageRecorder:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    provider: str = request.app.state.settings.upstream_provider
    return UsageRecorder(sessionmaker, provider)


def requested_model(body: bytes) -> str:
    """The model named in a request body that may not be valid; "" if none."""
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return ""
    model = payload.get("model") if isinstance(payload, dict) else None
    return model if isinstance(model, str) else ""
