# Architecture decision records

[Back to README](../README.md) · [Architecture](ARCHITECTURE.md) · [API](API.md) · [Database](DATABASE.md) · [Testing](TESTING.md)

Each record gives the context, the decision, and what it costs. They're short on purpose. The code comments at each site carry the detail.

- [ADR-001: Forward the raw request body](#adr-001-forward-the-raw-request-body)
- [ADR-002: Store money as integer micros](#adr-002-store-money-as-integer-micros)
- [ADR-003: SHA-256 for tokens, argon2id for passwords](#adr-003-sha-256-for-tokens-argon2id-for-passwords)
- [ADR-004: Opaque session cookies instead of JWTs](#adr-004-opaque-session-cookies-instead-of-jwts)
- [ADR-005: Copy org_id onto usage rows, guarded by a composite foreign key](#adr-005-copy-org_id-onto-usage-rows-guarded-by-a-composite-foreign-key)
- [ADR-006: 404 for organizations you aren't in](#adr-006-404-for-organizations-you-arent-in)
- [ADR-007: Record usage synchronously, for now](#adr-007-record-usage-synchronously-for-now)
- [ADR-008: Keyset pagination with signed cursors](#adr-008-keyset-pagination-with-signed-cursors)
- [ADR-009: Look up API keys on every request, without a cache](#adr-009-look-up-api-keys-on-every-request-without-a-cache)
- [ADR-010: Revoke keys, never delete them](#adr-010-revoke-keys-never-delete-them)
- [ADR-011: UUIDv7 primary keys](#adr-011-uuidv7-primary-keys)
- [ADR-012: Test against real Postgres](#adr-012-test-against-real-postgres)
- [ADR-013: Generate the frontend's API types from OpenAPI](#adr-013-generate-the-frontends-api-types-from-openapi)

## ADR-001: Forward the raw request body

**Context.** OpenAI's request schema is large and changes often: tools, response formats, reasoning options, and more. A proxy that parses the body into its own model and re-serializes it drops every field it doesn't know about.

**Decision.** Gate validates only what it needs (`model`, `stream`) and forwards the original bytes.

**Consequences.** New provider features work through Gate without code changes. Gate can't rewrite requests (for example, to force `include_usage` on streams), so a streaming client that doesn't ask for usage is recorded with zero tokens. A future rewrite would have to be explicit and narrow.

## ADR-002: Store money as integer micros

**Context.** Costs are small per request and summed across many requests. Floats drift, and `NUMERIC` is exact but slower and awkward across Python, JSON, and TypeScript.

**Decision.** All money is `BIGINT` millionths of a dollar. Prices are micros per 1,000 tokens. Cost is rounded half up to a whole micro, once, when the request is recorded.

**Consequences.** Sums are exact and fast. Every consumer has to divide by 1,000,000 for display. Sub-micro precision is lost per request, which is at most half a millionth of a dollar each.

## ADR-003: SHA-256 for tokens, argon2id for passwords

**Context.** Keys and session tokens have to be found by value on every request. Passwords only have to be verified at login and are guessable.

**Decision.** Tokens are 256 random bits hashed with SHA-256 and looked up by a unique index. Passwords use argon2id, run in a worker thread, with a dummy verify for unknown emails.

**Consequences.** Key lookup is one indexed query. A database leak exposes neither usable tokens nor cheaply crackable passwords. The security of SHA-256 here rests entirely on tokens being random, so tokens must never be user-chosen.

## ADR-004: Opaque session cookies instead of JWTs

**Context.** The dashboard needs login, logout, and expiry. JWTs avoid a database lookup, but they can't be revoked before they expire without a denylist, which is a database lookup again.

**Decision.** A random token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie, stored as a SHA-256 hash in `sessions` with an expiry.

**Consequences.** Logout deletes the row and takes effect immediately. One indexed query per dashboard request. Expired rows aren't cleaned up yet.

## ADR-005: Copy org_id onto usage rows, guarded by a composite foreign key

**Context.** Every reporting query filters by org. Joining through `api_keys` on every page is avoidable, but a copied `org_id` could disagree with the key's real org and put one tenant's traffic in another's reports.

**Decision.** `requests` and `usage_rollups` store `org_id`, and `(api_key_id, org_id)` references `api_keys (id, org_id)`.

**Consequences.** Reports read an `(org_id, created_at)` index directly, and the database rules out cross-org rows whatever the application does. The cost is an extra unique constraint on `api_keys`, and a key with history can't be deleted (see ADR-010).

## ADR-006: 404 for organizations you aren't in

**Context.** Returning 403 for someone else's org confirms that the org ID exists.

**Decision.** Non-members get the same 404 as for a missing org. Members without the required role get 403.

**Consequences.** Org IDs can't be probed. Tests assert the two 404 responses are identical. Debugging "why can't I see this org" is slightly harder, since the response never says "not a member".

## ADR-007: Record usage synchronously, for now

**Context.** Every request has to be recorded for reporting and, later, budgets. A queue and a worker would take the write off the response path, but they add infrastructure and a delay before usage appears.

**Decision.** Record in the request, in one transaction (request row, rollup upsert, `last_used_at`), best effort with a 5 second timeout. Failures are logged and never fail the proxied request. `request_id` is a unique idempotency key from the start.

**Consequences.** Usage shows up the moment a request finishes, and there's nothing else to run. Each non-streaming response waits for one database transaction. A database outage loses usage records instead of failing traffic, which is the right way round for a gateway. Moving to an async pipeline later only changes who calls `save_request`, and retries are already safe.

## ADR-008: Keyset pagination with signed cursors

**Context.** The request log grows without limit and gains rows while someone is paging through it. `OFFSET` gets slower with depth and shifts rows between pages as new ones arrive.

**Decision.** Page by `(created_at, id)`, newest first. The cursor holds the last row's sort key plus a fingerprint of the org and filters, signed with HMAC-SHA256 using `SECRET_KEY`.

**Consequences.** An unfiltered page is an index seek, and paging stays stable under inserts. Edited cursors, or cursors reused with other filters, get a 400 instead of a wrong page. There's no "jump to page N" or total count. `SECRET_KEY` must be the same on every instance and across restarts, or outstanding cursors stop working.

## ADR-009: Look up API keys on every request, without a cache

**Context.** Caching keys in memory would save one query per request, but a revoked key would keep working until the cache expired, on every instance.

**Decision.** No cache. Each proxy request looks up its key by hash, in a short-lived session that is released before forwarding.

**Consequences.** Revocation applies to the next request. One indexed lookup per request is cheap next to an LLM call. If this ever shows up in profiles, the fix is a short TTL cache with revocations pushed to instances, not a long TTL.

## ADR-010: Revoke keys, never delete them

**Context.** Requests and rollups reference keys. Deleting a key would either orphan that history or delete it with the key.

**Decision.** `DELETE /keys/{id}` sets `revoked_at`. The row stays, and revoking twice keeps the first timestamp.

**Consequences.** History stays attributable to a named key. Revoked keys stay in the list, marked as revoked. The schema backs this up: the foreign keys from `requests` and `usage_rollups` have no `ON DELETE` action, so deleting a key with history fails.

## ADR-011: UUIDv7 primary keys

**Context.** Sequential integers reveal row counts and are guessable. UUIDv4 values are random, so inserts land on random index pages.

**Decision.** UUIDv7 via `uuid-utils` (the standard library only gains `uuid7` in Python 3.14).

**Consequences.** IDs are safe to expose and insert in time order. `request_id` being time-ordered also lets the seed script generate realistic historical IDs.

## ADR-012: Test against real Postgres

**Context.** The invariants that matter (composite foreign keys, upserts, `citext`, time-zone-sensitive checks) are enforced by Postgres. SQLite would pass tests that Postgres fails, and the reverse.

**Decision.** Tests use real Postgres, with one database per xdist worker, rolled-back transactions for most tests, and committed sessions where the app needs them. Password hashing uses argon2's cheapest parameters in tests only.

**Consequences.** Contributors need Postgres running. Without it, database tests skip, except when `CI` is set, where they fail. Runtime stays manageable through parallel workers and a shared connection pool.

## ADR-013: Generate the frontend's API types from OpenAPI

**Context.** Hand-written TypeScript types for API responses drift from the backend without anyone noticing until something breaks at runtime.

**Decision.** `python -m gate.openapi` exports the OpenAPI document, openapi-typescript turns it into `schema.d.ts`, and openapi-fetch uses those types. `npm run check:api` fails when the committed files are stale. A backend test also checks that every route documents its success schema and error responses, so the generated types are complete.

**Consequences.** A backend field rename is a frontend compile error. The generated files are committed and must be regenerated after API changes.
