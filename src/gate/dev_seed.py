"""Development data: a user, an org with three API keys, and a month of traffic.

    DEV=true uv run python -m gate.dev_seed

Then sign in as dev@example.com with password "correct horse battery".

Safe to run repeatedly. Prices, the user, the org and the keys are created once
and left alone after that. The traffic (requests and their usage rollups) is
replaced on every run, so it always covers the 30 days before the run. It is
generated from a fixed random seed, so each run makes the same mix.

A script rather than a migration, so production databases never get prices
for mock models, and it refuses to run unless DEV is set. The mock provider
answers to any model name; these are the ones to use.
"""

import asyncio
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from gate.config import Settings
from gate.db import create_engine, create_sessionmaker
from gate.models import (
    ApiKey,
    Membership,
    ModelPrice,
    Organization,
    RequestLog,
    UsageRollup,
    User,
)
from gate.security import API_KEY_DISPLAY_LENGTH, hash_password, hash_token
from gate.usage import RequestRow, TokenCounts, save_request

EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)

# (model, input micros per 1k tokens, output micros per 1k tokens)
MOCK_PRICES = [
    ("mock-1", 500, 1500),
    ("mock-mini", 150, 600),
    ("mock-large", 2500, 10000),
]

EMAIL = "dev@example.com"
PASSWORD = "correct horse battery"
REQUEST_COUNT = 500
DAYS = 30
RANDOM_SEED = 6

# Fixed ids, so reruns find what an earlier run made.
NAMESPACE = uuid.UUID("0d7f3c2e-6a51-4d0e-9d1b-5b8f1e3a7c90")
USER_ID = uuid.uuid5(NAMESPACE, "user")
ORG_ID = uuid.uuid5(NAMESPACE, "org")


@dataclass(frozen=True)
class SeedKey:
    name: str
    rpm_limit: int
    monthly_budget_micros: int | None
    share: float  # of the generated traffic

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(NAMESPACE, f"key:{self.name}")

    @property
    def plaintext(self) -> str:
        # Fixed, so a dev can keep using it across reseeds. Only ever for DEV.
        return f"gk_dev_{self.name}_" + "0" * 24


KEYS = [
    SeedKey("production", rpm_limit=600, monthly_budget_micros=500_000_000, share=0.6),
    SeedKey("staging", rpm_limit=120, monthly_budget_micros=50_000_000, share=0.25),
    SeedKey("ci", rpm_limit=30, monthly_budget_micros=None, share=0.15),
]

# (model, share of traffic, typical prompt tokens, typical completion tokens)
MODEL_MIX = [
    ("mock-1", 0.5, 900, 350),
    ("mock-mini", 0.35, 400, 150),
    ("mock-large", 0.15, 2500, 800),
]


async def seed_prices(session: AsyncSession) -> None:
    await session.execute(
        insert(ModelPrice)
        .values(
            [
                {
                    "model": model,
                    "effective_from": EFFECTIVE_FROM,
                    "input_micros_per_1k": input_per_1k,
                    "output_micros_per_1k": output_per_1k,
                }
                for model, input_per_1k, output_per_1k in MOCK_PRICES
            ]
        )
        .on_conflict_do_nothing()
    )


async def seed_account(session: AsyncSession) -> None:
    """The dev user, owner of an org holding the seed keys."""
    if await session.get(User, USER_ID) is None:
        existing = await session.scalar(select(User.id).where(User.email == EMAIL))
        if existing is not None:
            raise SystemExit(
                f"{EMAIL} already exists but wasn't made by this script; "
                "delete it or reset the database, then rerun."
            )
        session.add(
            User(id=USER_ID, email=EMAIL, password_hash=await hash_password(PASSWORD))
        )
        await session.flush()
    await session.execute(
        insert(Organization)
        .values(id=ORG_ID, name="Acme (dev)")
        .on_conflict_do_nothing()
    )
    await session.execute(
        insert(Membership)
        .values(user_id=USER_ID, org_id=ORG_ID, role="owner")
        .on_conflict_do_nothing()
    )
    await session.execute(
        insert(ApiKey)
        .values(
            [
                {
                    "id": key.id,
                    "org_id": ORG_ID,
                    "name": key.name,
                    "prefix": key.plaintext[:API_KEY_DISPLAY_LENGTH],
                    "key_hash": hash_token(key.plaintext),
                    "rpm_limit": key.rpm_limit,
                    "monthly_budget_micros": key.monthly_budget_micros,
                }
                for key in KEYS
            ]
        )
        .on_conflict_do_nothing()
    )


def around(rng: random.Random, typical: int) -> int:
    """A positive count near `typical`, with a long tail upwards."""
    return max(1, round(rng.lognormvariate(0, 0.6) * typical))


def generate_traffic(now: datetime) -> list[RequestRow]:
    rng = random.Random(RANDOM_SEED)
    rows = []
    for _ in range(REQUEST_COUNT):
        key = rng.choices(KEYS, weights=[k.share for k in KEYS])[0]
        model, _, prompt, completion = rng.choices(
            MODEL_MIX, weights=[m[1] for m in MODEL_MIX]
        )[0]
        created_at = now - timedelta(seconds=rng.uniform(0, DAYS * 86400))
        streamed = rng.random() < 0.4
        outcome = rng.random()
        if outcome < 0.9:
            status_code = 200
            tokens = TokenCounts(around(rng, prompt), around(rng, completion))
            # Time to generate the completion, plus a little overhead.
            latency_ms = 150 + tokens.completion_tokens * rng.randint(8, 20)
        elif outcome < 0.96:
            # Rate limited: rejected before any tokens were produced.
            status_code, streamed = 429, False
            tokens = TokenCounts()
            latency_ms = rng.randint(3, 25)
        else:
            # The provider couldn't be reached, sometimes only after a while.
            status_code, streamed = 502, False
            tokens = TokenCounts()
            latency_ms = rng.choice([rng.randint(20, 400), rng.randint(5_000, 30_000)])
        rows.append(
            RequestRow(
                request_id=uuid7(nanoseconds=int(created_at.timestamp() * 1e9)),
                org_id=ORG_ID,
                api_key_id=key.id,
                model=model,
                provider="mock",
                status_code=status_code,
                tokens=tokens,
                latency_ms=latency_ms,
                streamed=streamed,
                created_at=created_at,
            )
        )
    return sorted(rows, key=lambda row: row.created_at)


async def seed_traffic(session: AsyncSession, now: datetime) -> None:
    key_ids = [key.id for key in KEYS]
    await session.execute(delete(RequestLog).where(RequestLog.api_key_id.in_(key_ids)))
    await session.execute(
        delete(UsageRollup).where(UsageRollup.api_key_id.in_(key_ids))
    )
    await session.execute(
        update(ApiKey).where(ApiKey.id.in_(key_ids)).values(last_used_at=None)
    )
    # The same write path as live traffic, so rollups and costs match exactly.
    for row in generate_traffic(now):
        await save_request(session, row)


async def seed(database_url: str, now: datetime | None = None) -> None:
    engine = create_engine(database_url)
    try:
        async with create_sessionmaker(engine).begin() as session:
            await seed_prices(session)
            await seed_account(session)
            await seed_traffic(session, now or datetime.now(UTC))
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    if not settings.dev:
        sys.exit("Refusing to seed: this is development data. Set DEV=true.")
    asyncio.run(seed(settings.database_url))
    print(f"Seeded {REQUEST_COUNT} requests over the last {DAYS} days.")
    print(f"Sign in as {EMAIL} / {PASSWORD}")
    for key in KEYS:
        print(f"  {key.name:<11} {key.plaintext}")


if __name__ == "__main__":
    main()
