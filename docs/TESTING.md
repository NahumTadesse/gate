# Testing

[Back to README](../README.md) · [Architecture](ARCHITECTURE.md) · [API](API.md) · [Database](DATABASE.md) · [Decisions](DECISIONS.md)

292 tests: 229 backend (pytest, `tests/`) and 63 frontend (Vitest, `frontend/src/**/*.test.tsx`).

```sh
uv run pytest                  # backend, in parallel
uv run ruff check
uv run mypy src tests
cd frontend && npm run check   # oxlint, tsc, vitest, production build
```

A change counts as done only when pytest, ruff, and mypy all pass ([CLAUDE.md](../CLAUDE.md)).

## Principles

**Real Postgres, not SQLite or mocks.** The behavior worth testing lives in the database: composite foreign keys, `ON CONFLICT` upserts, `SELECT ... FOR UPDATE`, `citext`, CHECK constraints that depend on time zones, and asyncpg's type handling. SQLite has none of these, or has them differently, so passing on SQLite would say little.

**Fake the network, not the code.** The provider is faked with `httpx.MockTransport` (a function from request to response) or with the real mock provider app via `httpx.ASGITransport`. Gate's own code runs unmodified. The frontend does the same with MSW, which intercepts `fetch`, so tests go through the real API client and its middleware.

**Test what the user or client sees.** API tests assert on status codes and response bodies. Frontend tests find elements by role and label, as a user or screen reader would, and cover each page's loading, empty, error, and success states.

## Backend fixtures

Defined in `tests/conftest.py`.

| Fixture | What it gives | Used for |
| --- | --- | --- |
| `test_database_url` | A migrated database per xdist worker (`gate_test_gw0`, `gate_test_gw1`, ...), created on first use | Isolation between parallel workers |
| `db_session` | A session inside a transaction that's rolled back after the test. Commits in the code under test only release a savepoint. | Most database tests. Fast and needs no cleanup. |
| `committed_sessionmaker` | Sessions that really commit, each on its own connection. Tables are emptied before and after. | API tests, where the app opens its own sessions and each request commits |
| `api_app` | The whole app on the test database, with a recording fake upstream | API and recording tests |
| `signup` | Registers and logs in `<name>@example.com` and returns a client carrying their cookie and their org ID | Authorization tests with several users |
| `make_client` | The app with API key auth and recording replaced by fakes, so no database is needed | Proxy tests that aren't about auth or recording |

Two session-wide settings keep the suite honest and fast:

- **Production settings.** `SECRET_KEY` is set and `DEV` is unset for every test, so code paths that only work in dev mode fail.
- **Cheap argon2.** Password hashing uses argon2's cheapest parameters. The algorithm and code path are the same, only the work factors drop. Production parameters cost about 180ms per hash and dominated the runtime.

If Postgres isn't reachable, database tests are skipped, so the pure tests still run on a machine without it. With the `CI` environment variable set, they fail instead, so a misconfigured pipeline can't pass by skipping.

## What each file covers

| File | Tests | Covers |
| --- | --- | --- |
| `test_proxy.py` | 7 | Forwarding, pass-through of upstream status and unknown fields, 502 and 504 mapping |
| `test_proxy_streaming.py` | 16 | Relay unchanged with SSE headers, first chunk sent before upstream finishes, failures before the first chunk, mid-stream error events (including after a partial event), client disconnect cancelling upstream, usage parsing across chunk boundaries and CRLF |
| `test_proxy_auth.py` | 9 | Valid, missing, unknown, and revoked keys. Session cookies don't work on the proxy. Auth is checked before the body. Streams don't hold a database connection. |
| `test_usage_recording.py` | 20 | Rows and rollups written, same-hour rollups combined, stream usage, pricing at the price in effect, unpriced models, idempotent `request_id`, failed and revoked requests recorded, unknown keys not recorded, a database failure not breaking the response, the seed script producing consistent data |
| `test_usage_models.py` | 14 | Schema defaults, cross-org rows rejected in both tables, rollup upsert, hour-bucket CHECK under an unusual session time zone, indexes present |
| `test_auth.py` | 23 | Registration, login cookie flags, only hashes stored, case-insensitive email, expired and unknown sessions, logout scope |
| `test_orgs.py` | 21 | Non-members get 404 indistinguishable from missing orgs, another org's key unreachable through your own org path, each role's permissions, last owner protected, key shown once and stored hashed, revoke idempotent |
| `test_requests_api.py` | 40 | Paging covers every row once in order, rows inserted mid-walk don't shift pages, filters hold across pages, tampered or rebound cursors rejected, org isolation |
| `test_usage_api.py` | 14 | Hourly and daily totals, UTC days, grouping by model and key, range bounds, org isolation |
| `test_errors.py` | 23 | OpenAI error shape on the proxy and FastAPI's on the management API, and every route documenting its errors in OpenAPI |
| `test_models.py` | 7 | UUIDv7 IDs, unique emails ignoring case, role CHECK, cascades, `raise_on_sql` relationships |
| `test_config.py` | 13 | `SECRET_KEY` required, minimum length, dev fallback, not revealed in reprs |
| `test_mock_provider.py` | 10 | The mock provider's response shape, streaming, and optional usage chunk |
| `test_integration.py` | 2 | Gate against the real mock provider app, streaming and not |
| `test_migrations.py` | 1 | No diff between migrations and models, and a full downgrade and upgrade round trip |
| `test_health.py` | 4 | `/healthz` doesn't touch the database, `/readyz` reports it up or down |
| `test_db.py` | 4 | The fixtures themselves: rollback isolation, committed visibility, table clearing |
| `test_openapi_export.py` | 1 | The OpenAPI export script runs |

## Frontend

| File | Tests | Covers |
| --- | --- | --- |
| `ApiKeysPage.test.tsx` | 14 | States, members see no controls, form validation, plaintext key shown once with a copy control, API failure in the dialog, revoke only after confirmation |
| `MembersPage.test.tsx` | 15 | States, owner controls, email validation, unknown user, add, change role, refused role change, remove after confirmation |
| `RequestsPage.test.tsx` | 13 | States, filters in the URL and the query, cursor paging both ways, filters resetting paging, recovery from a rejected cursor |
| `OverviewPage.test.tsx` | 11 | States, totals, the chart and its table view, range changes |
| `AuthPages.test.tsx` | 11 | Validation, wrong credentials, return to the requested page, duplicate email, register then land on the org |
| `LandingPage.test.tsx` | 6 | Renders signed out and with the API down, signed-in redirect, call to action, code sample tabs by click and arrow keys |
| `AppShell.test.tsx` | 2 | Signed-out redirect, redirect to login when the session expires mid-visit |

## What isn't tested

- **Real browsers.** Frontend tests run in jsdom. There are no Playwright or Cypress end-to-end tests yet.
- **Real providers.** Every test uses a fake or the bundled mock provider. Gate can't call an authenticated provider yet (see [planned](../README.md#built-and-planned)).
- **Concurrency.** The owner-row lock that stops two concurrent demotions from removing the last owner has no test that actually races two requests. There are no load tests or benchmarks either.
- **CI.** The suite runs locally. There is no CI pipeline yet.
