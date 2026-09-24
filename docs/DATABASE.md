# Database

[Back to README](../README.md) · [Architecture](ARCHITECTURE.md) · [API](API.md) · [Testing](TESTING.md) · [Decisions](DECISIONS.md)

PostgreSQL, accessed through async SQLAlchemy 2 and asyncpg. The schema is defined in `src/gate/models.py` and migrated with Alembic (`migrations/versions/`). The ER diagram is in the [README](../README.md#database).

## Conventions

- **Primary keys are UUIDv7** (`uuid-utils`). They are time-ordered, so new rows land at the end of the B-tree index instead of at random pages as UUIDv4 would. They are also safe to expose in URLs, unlike sequential integers, which reveal row counts.
- **Timestamps are `timestamptz`.** `created_at` has a server default of `now()` and is fetched back with `RETURNING` (`eager_defaults`), because async SQLAlchemy can't lazily refresh it later.
- **Constraint names are deterministic** (a naming convention on the metadata), so Alembic can diff and drop them by name.
- **Relationships use `lazy="raise_on_sql"`.** An implicit lazy load would fail under asyncio anyway. This makes it fail loudly and immediately, and queries load what they need explicitly.
- **Counters have server defaults**, so raw `INSERT` and upsert statements from the usage path get them too.

## Tables

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | PK |
| `email` | citext | Unique. `citext` makes the uniqueness and lookups case-insensitive in the database, not by lowercasing in application code. |
| `password_hash` | text | argon2id, with parameters encoded in the hash. Rehashed on login if the parameters change. |
| `created_at` | timestamptz | |

### `organizations`

`id`, `name`, `created_at`. Registering creates a personal org owned by the new user.

### `memberships`

| Column | Type | Notes |
| --- | --- | --- |
| `user_id` | uuid | PK, FK to `users`, `ON DELETE CASCADE` |
| `org_id` | uuid | PK, FK to `organizations`, `ON DELETE CASCADE` |
| `role` | text | `CHECK (role IN ('owner', 'admin', 'member'))` |
| `created_at` | timestamptz | |

The composite primary key makes a user a member of an org at most once. Text with a CHECK rather than a Postgres enum type: enum values can be added but never removed, while a CHECK can be replaced in an ordinary migration.

### `sessions`

| Column | Type | Notes |
| --- | --- | --- |
| `token_hash` | text | PK. SHA-256 of the cookie value. The cookie itself is never stored. |
| `user_id` | uuid | FK to `users`, `ON DELETE CASCADE`, indexed |
| `expires_at` | timestamptz | Checked in the lookup query (`expires_at > now()`) |
| `created_at` | timestamptz | |

Expired rows are not cleaned up yet. They can't authenticate, but they accumulate.

### `api_keys`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | PK |
| `org_id` | uuid | FK to `organizations`, `ON DELETE CASCADE`, indexed |
| `name` | text | |
| `prefix` | text | First 8 characters of the key (`gk_` plus 5), so people can tell keys apart in the list |
| `key_hash` | text | Unique. SHA-256 of the key. |
| `rpm_limit` | int | Default 60. Stored, not enforced yet. |
| `monthly_budget_micros` | bigint | Null means no budget. Stored, not enforced yet. |
| `revoked_at` | timestamptz | Null while active. Set once. Revoking again keeps the first time. |
| `last_used_at` | timestamptz | Updated by recording, only moving forward |
| `created_at` | timestamptz | |

Also `UNIQUE (id, org_id)`. It is redundant as a uniqueness rule, since `id` is already the primary key, but a composite foreign key needs a unique target, and this is that target.

Keys are revoked, never deleted, because `requests` and `usage_rollups` reference them.

### `requests`

One row per proxied request that reached a real key.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | PK |
| `request_id` | uuid | Unique. The idempotency key: inserts use `ON CONFLICT (request_id) DO NOTHING`. |
| `org_id` | uuid | Copied from the key. See below. |
| `api_key_id` | uuid | |
| `model` | text | As requested by the client |
| `provider` | text | From `UPSTREAM_PROVIDER` |
| `status_code` | smallint | What the client got, including 401 for revoked keys and 502 or 504 for upstream failures |
| `prompt_tokens`, `completion_tokens` | int | From the provider's `usage` object only. 0 if absent. |
| `cost_micros` | bigint | Priced at write time |
| `latency_ms` | int | From request start until recording. For streams, that's the full stream duration. |
| `streamed` | bool | |
| `created_at` | timestamptz | The request's start time, set by the recorder |

Constraints and indexes:

- `FOREIGN KEY (api_key_id, org_id) REFERENCES api_keys (id, org_id)`. No `ON DELETE` action, so a key with history can't be deleted.
- `ix_requests_org_id_created_at` on `(org_id, created_at DESC)`, for the request log.
- `ix_requests_api_key_id_created_at` on `(api_key_id, created_at DESC)`, for per-key history.

### `usage_rollups`

Hourly totals per key and model, maintained by an upsert on every recorded request.

| Column | Type | Notes |
| --- | --- | --- |
| `api_key_id` | uuid | PK part |
| `model` | text | PK part |
| `bucket_start` | timestamptz | PK part. `CHECK (extract(epoch FROM bucket_start) % 3600 = 0)`. |
| `org_id` | uuid | Same composite FK to `api_keys (id, org_id)` as `requests` |
| `request_count` | int | |
| `tokens` | bigint | Prompt plus completion |
| `cost_micros` | bigint | |

The hour check uses epoch seconds, not `date_trunc('hour', ...)`. `date_trunc` on a `timestamptz` depends on the session's time zone, so it would reject valid UTC hours for sessions in a zone like `+05:30`. A test runs the check under such a zone.

Usage reports filter rollups by `org_id` and a `bucket_start` range, and there's no index for that yet. It's fine at development volumes, and `(org_id, bucket_start)` is the index to add first.

### `model_prices`

| Column | Type | Notes |
| --- | --- | --- |
| `model` | text | PK part |
| `effective_from` | timestamptz | PK part |
| `input_micros_per_1k` | bigint | Price per 1,000 prompt tokens |
| `output_micros_per_1k` | bigint | Price per 1,000 completion tokens |

A price applies from `effective_from` until the next row for the same model. A request is priced at the row with the latest `effective_from` at or before the request's start, so a price change never reprices history. A model with no price costs 0. There is no API for prices yet. The seed script inserts prices for the mock models.

## Decisions

The four decisions from the README, in more depth.

### Money as integer micros

All money is `BIGINT` millionths of a dollar. `0.1 + 0.2 != 0.3` in binary floating point, and a sum over thousands of float costs drifts in ways that show up as a spend total that doesn't match the per-request rows. `NUMERIC` is exact, but its arithmetic is slower than `int8`, and it arrives in Python as `Decimal`, which then needs care in JSON and in the TypeScript client. Integers are exact, fast, and serialize as plain JSON numbers.

A micro is small enough for per-token pricing. Prices are stored per 1,000 tokens, so a model at $0.15 per million input tokens is 150 micros per 1k. Cost is computed as `(prompt * in + completion * out + 500) // 1000`: one rounding step, half up, once per request. `BIGINT` holds about 9.2 × 10¹⁸ micros, or 9.2 trillion dollars.

The API returns micros and the dashboard converts for display. Clients never see a float amount they might sum.

### SHA-256 for keys and tokens, argon2id for passwords

The two kinds of secret need different hashes because they're attacked differently.

API keys and session tokens are 32 bytes from `secrets.token_urlsafe`, so 256 bits of randomness. Guessing one is infeasible whatever the hash speed, so a fast hash loses nothing. It also has to be deterministic: each proxy request finds its key with `WHERE key_hash = sha256(token)` on a unique index. argon2 salts each hash randomly, so finding a key would mean running a deliberately slow verify against every stored key.

Passwords are chosen by people and low in entropy, so a leaked hash invites offline guessing. argon2id makes each guess cost memory and time. It runs in a worker thread so its tens of milliseconds of CPU don't stall the event loop. Logging in with an unknown email verifies against a dummy hash, so the response takes the same time and doesn't reveal which emails have accounts.

### `org_id` copied onto `requests` and `usage_rollups`

`org_id` can be derived from `api_key_id`, so storing it on `requests` is denormalization. It's there because every reporting query starts with "this org's rows, newest first" or "this org's rows in this range". With `org_id` on the row, the request log reads `ix_requests_org_id_created_at` directly and stops after `limit + 1` rows. Without it, every page would join through `api_keys` and filter there. The key's org never changes, so the copy can't go stale.

### A composite foreign key preventing cross-org rows

A copy of `org_id` is only safe if it can't disagree with the key's real org. Separate FKs on `api_key_id` and `org_id` wouldn't ensure that: both would be satisfied by key A from org 1 paired with org 2. That row would count org 1's traffic in org 2's reports and bill it to them.

So `(api_key_id, org_id)` together reference `api_keys (id, org_id)`. The pair must exist as a pair, which is only true when the key belongs to that org. `org_id` needs no FK of its own, because `api_keys.org_id` already guarantees the org exists. The migration that added the constraint fails if any existing row violates it, and tests insert mismatched rows into both tables and assert that the database rejects them.

## Migrations

```sh
uv run alembic upgrade head                           # apply
uv run alembic revision --autogenerate -m "Add ..."   # new migration from model changes
uv run alembic downgrade -1                           # undo the last one
```

`migrations/env.py` reads `DATABASE_URL` from settings. The test suite passes its own URL through Alembic's config attributes instead.

`tests/test_migrations.py` checks that the migrated schema has no autogenerate diff from the models, downgrades to empty, upgrades to head again, and checks once more. A model change without a migration, or a migration that doesn't reverse cleanly, fails the suite.
