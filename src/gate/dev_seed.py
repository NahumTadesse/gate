"""Seed prices for the mock provider's models, so development traffic has a cost.

    uv run python -m gate.dev_seed

Safe to run repeatedly: rows already present are left alone. A script rather
than a migration, so production databases never get prices for mock models.
The mock provider answers to any model name; these are the ones to use.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from gate.config import Settings
from gate.db import create_engine, create_sessionmaker
from gate.models import ModelPrice

EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)

# (model, input micros per 1k tokens, output micros per 1k tokens)
MOCK_PRICES = [
    ("mock-1", 500, 1500),
    ("mock-mini", 150, 600),
    ("mock-large", 2500, 10000),
]


async def seed(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with create_sessionmaker(engine).begin() as session:
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
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(seed(Settings().database_url))
    print(f"Seeded prices for {', '.join(model for model, *_ in MOCK_PRICES)}")


if __name__ == "__main__":
    main()
