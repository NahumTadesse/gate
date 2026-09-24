# Gate

Gate is a self-hosted gateway that sits between your applications and an LLM provider. Every request goes through it, so each call is tied to a key and an organization, recorded, and priced.

Teams that call LLM APIs directly tend to share one provider key across services, people, and environments. When the bill arrives, nobody can say which service spent the money, a leaked key can only be stopped by rotating it everywhere, and there is no request history to debug from. Gate gives each service its own key, which can be revoked on its own. It records every request with its tokens, cost, latency, and status, and shows spend per key and per model in a dashboard. Clients keep using the OpenAI SDK they already have. Only the base URL and the key change.

![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Tests: 292](https://img.shields.io/badge/tests-229%20backend%20%2B%2063%20frontend-2E7D32)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy-1F5082)
![Lint: ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)

<!--
  Build status and license badges go here once they exist. There is no CI
  workflow or LICENSE file yet, and a badge for either would be false.
-->

## Contents

- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Using it from an existing client](#using-it-from-an-existing-client)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Database](#database)
- [Testing](#testing)
- [Built and planned](#built-and-planned)
- [AI-assisted development](#ai-assisted-development)
- [Local development](#local-development)
- [Further reading](#further-reading)

## Screenshots

| Landing page | Overview dashboard | API keys |
| --- | --- | --- |
| ![Landing page](docs/images/landing.png) | ![Overview dashboard](docs/images/overview.png) | ![API keys page](docs/images/api-keys.png) |

## Quick start

You need [uv](https://docs.astral.sh/uv/), Docker (only to run Postgres), and curl. This runs Gate against the bundled mock provider, which echoes your last message back and reports token usage like OpenAI does.

```sh
git clone https://github.com/NahumTadesse/gate.git
cd gate

# Postgres 17
docker run -d --name gate-postgres -p 5432:5432 \
  -e POSTGRES_PASSWORD=devpassword -e POSTGRES_DB=gate postgres:17

# DEV=true lets Gate start without a SECRET_KEY. Local use only.
cp .env.example .env
echo "DEV=true" >> .env

uv sync
uv run alembic upgrade head
uv run python -m gate.dev_seed   # a dev user, three API keys, 30 days of traffic
```

Start the mock provider and Gate, each in its own terminal:

```sh
uv run uvicorn gate.mock_provider:create_app --factory --port 8001
```

```sh
uv run uvicorn gate.main:create_app --factory --port 8000
```

Send a request through Gate with one of the seeded keys:

```sh
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gk_dev_production_000000000000000000000000" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock-1", "messages": [{"role": "user", "content": "Hello through Gate"}]}'
```

```json
{"id":"chatcmpl-...","object":"chat.completion","created":1790211746,"model":"mock-1","choices":[{"index":0,"message":{"role":"assistant","content":"Hello through Gate"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":3,"total_tokens":6}}
```

That request is now in the log. Sign in and read it back:

```sh
curl -c cookies.txt http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@example.com", "password": "correct horse battery"}'

ORG_ID=$(curl -s -b cookies.txt http://localhost:8000/api/v1/me \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["orgs"][0]["id"])')

curl -b cookies.txt "http://localhost:8000/api/v1/orgs/$ORG_ID/requests?limit=1"
```

To see the dashboard, run `npm install && npm run dev` in `frontend/` (Node 20.19+ or 22.12+), open http://localhost:5173, and sign in as `dev@example.com` / `correct horse battery`. Interactive API docs are at http://localhost:8000/docs.

## Using it from an existing client

Gate speaks the OpenAI chat completions API, so an existing OpenAI client only needs a different `base_url` and a Gate key:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",  # the one-line change
    api_key="gk_dev_production_000000000000000000000000",
)

reply = client.chat.completions.create(
    model="mock-1",
    messages=[{"role": "user", "content": "Hello through Gate"}],
)
print(reply.choices[0].message.content)
```

Streaming works the same way. Pass `stream_options={"include_usage": True}` so the provider reports token counts at the end of the stream; Gate records whatever usage the provider sends and never estimates it.

Two limits apply today. Gate proxies `POST /v1/chat/completions` only, not embeddings, models, or other endpoints. It also doesn't yet attach a provider API key to the requests it forwards, so the upstream has to be one that doesn't need one, like the bundled mock. Upstream credentials are the first item under [planned](#built-and-planned).

## Architecture

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (OpenAI SDK)
    participant G as Gate (FastAPI)
    participant DB as Postgres
    participant P as Provider

    C->>G: POST /v1/chat/completions<br/>Authorization: Bearer gk_...
    G->>DB: find key by SHA-256 of the token
    alt missing, unknown, or revoked key
        G-->>DB: record the 401 (revoked keys only)
        G-->>C: 401 invalid_api_key
    else body not a chat completion request
        G-->>C: 422 invalid_request_error
    else valid
        G->>P: forward the body unchanged
        alt stream is false
            P-->>G: JSON completion with usage
            G->>DB: record request, rollup, last_used_at
            G-->>C: provider's status and body
        else stream is true
            P-->>G: SSE chunks
            G-->>C: relay each chunk as it arrives
            Note over G: watch chunks for a usage object
            G->>DB: record once the stream ends or the client leaves
        end
    end
```

What happens on each request:

1. **Authenticate.** Gate hashes the bearer token with SHA-256 and looks it up by a unique index. It uses a short-lived database session for this and releases it straight away, so a long stream never holds a pooled connection. There is no cache, so a key revoked in the dashboard fails on its very next request.
2. **Validate.** Gate reads only `model` and `stream` from the body. The rest is forwarded byte for byte, so provider features Gate doesn't know about still work. Errors on the proxy use OpenAI's error shape, so SDKs raise their usual exceptions.
3. **Forward.** A single `httpx.AsyncClient`, created at startup, sends the request upstream with a 5 second connect timeout and a 60 second limit on each read, so a long stream is fine as long as chunks keep arriving. Upstream errors pass through with their status and body. A timeout becomes 504 and an unreachable provider becomes 502.
4. **Stream.** For `stream: true`, Gate sends each chunk on as soon as it arrives. An incremental SSE parser watches the chunks for the usage object. If the provider fails mid-stream, the client gets a final SSE error event, because the 200 status has already been sent. If the client disconnects, Gate closes the upstream request so the provider stops generating tokens.
5. **Record.** When the response is done, one transaction prices the request at the model's price in effect at that moment, inserts a row into `requests`, adds it to the hourly `usage_rollups` row for that key and model, and bumps the key's `last_used_at`. Recording is best effort. A failure is logged and never fails the proxied request, and a 5 second timeout stops a slow database from holding the response.

The management API under `/api/v1` serves the dashboard: accounts, cookie sessions, members and roles, keys, the request log, and usage reports. More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/API.md](docs/API.md).

## Tech stack

| Tool | Used for | Why this over the alternative |
| --- | --- | --- |
| ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) | HTTP API and proxy | Async end to end, which a proxy holding many open streams needs. Its dependency injection carries auth (`get_api_key`, `require_role`) and its OpenAPI output generates the frontend's types. Flask is sync-first, and Django's ORM still runs queries in worker threads behind its async API. |
| ![httpx](https://img.shields.io/badge/httpx-2F4F4F?logo=python&logoColor=white) | Calling the provider | Async streaming with `client.stream()`, plus `MockTransport` and `ASGITransport`, which let tests swap in a fake or the real mock provider in process without opening a socket. aiohttp's client has no hook to route requests into an in-process ASGI app. |
| ![PostgreSQL](https://img.shields.io/badge/PostgreSQL_17-4169E1?logo=postgresql&logoColor=white) | All state | Composite foreign keys, CHECK constraints, `INSERT ... ON CONFLICT` upserts for rollups, `citext` for case-insensitive emails, and `SELECT ... FOR UPDATE` for the last-owner rule. Transactional DDL means a failed migration rolls back cleanly, which MySQL can't do. |
| ![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy_2-D71F00?logo=sqlalchemy&logoColor=white) + asyncpg | ORM, queries, driver | Typed `Mapped[]` models that mypy checks, Core `insert().on_conflict_do_update()` for upserts, and metadata that Alembic can diff against the database. Raw asyncpg would mean hand-written SQL with no schema diffing. |
| ![Alembic](https://img.shields.io/badge/Alembic-6BA81E?logo=python&logoColor=white) | Migrations | A test downgrades to empty, upgrades to head, and asserts no diff against the models, so migrations can't drift from the code. |
| ![Pydantic](https://img.shields.io/badge/Pydantic_2-E92063?logo=pydantic&logoColor=white) | Validation and settings | One model per request body, generating both validation and OpenAPI. pydantic-settings refuses to start without a 32+ character `SECRET_KEY` unless `DEV` is set. |
| ![argon2](https://img.shields.io/badge/argon2--cffi-4B5563) | Password hashing | argon2id is memory-hard, which makes GPU cracking expensive, and it is OWASP's first recommendation for password storage. bcrypt only uses the first 72 bytes of a password. |
| ![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white) | Dependencies and running | One tool for the lockfile, virtualenv, and running commands, and it resolves and installs far faster than pip or Poetry. |
| ![pytest](https://img.shields.io/badge/pytest-0A9EDC?logo=pytest&logoColor=white) + xdist | Backend tests | Fixtures scoped per session and per test make a real Postgres affordable. xdist runs workers in parallel, each with its own database. |
| ![Ruff](https://img.shields.io/badge/Ruff-D7FF64?logo=ruff&logoColor=black) + ![mypy](https://img.shields.io/badge/mypy-1F5082?logo=python&logoColor=white) | Lint and types | Ruff is one fast linter in place of flake8 and its plugins. mypy, with the Pydantic plugin, checks `src` and `tests`. Both are part of the definition of done in CLAUDE.md. |
| ![React](https://img.shields.io/badge/React_19-61DAFB?logo=react&logoColor=black) + ![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white) + ![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white) | Dashboard | The dashboard needs no server rendering, so a Vite SPA avoids running a Next.js server. Vite proxies `/api` in development so the session cookie stays same-origin. |
| ![TanStack Query](https://img.shields.io/badge/TanStack_Query-FF4154?logo=reactquery&logoColor=white) | Server state | Caching, retries, and invalidating the key list after a create or revoke, without writing a store by hand as with Redux. |
| openapi-typescript + openapi-fetch | Typed API client | Types come from the backend's OpenAPI document, and `npm run check:api` fails if they're stale. openapi-fetch is a thin typed `fetch`, unlike generators that emit a client class per endpoint. |
| ![Vitest](https://img.shields.io/badge/Vitest-6E9F18?logo=vitest&logoColor=white) + Testing Library + ![MSW](https://img.shields.io/badge/MSW-FF6A33?logo=mockserviceworker&logoColor=white) | Frontend tests | MSW intercepts at the network layer, so tests go through the real API client, including its 401 redirect middleware, instead of mocking modules. |
| ![Recharts](https://img.shields.io/badge/Recharts-22B5BF) | Spend chart | Declarative React components that render SVG. Chart.js draws to a canvas and needs a wrapper to fit React's model. |

## Database

```mermaid
erDiagram
    users ||--o{ memberships : has
    organizations ||--o{ memberships : has
    users ||--o{ sessions : has
    organizations ||--o{ api_keys : owns
    api_keys ||--o{ requests : "made (api_key_id, org_id)"
    api_keys ||--o{ usage_rollups : "totals (api_key_id, org_id)"

    users {
        uuid id PK "UUIDv7"
        citext email UK
        text password_hash "argon2id"
        timestamptz created_at
    }
    organizations {
        uuid id PK
        text name
        timestamptz created_at
    }
    memberships {
        uuid user_id PK, FK
        uuid org_id PK, FK
        text role "owner, admin, member"
        timestamptz created_at
    }
    sessions {
        text token_hash PK "SHA-256"
        uuid user_id FK
        timestamptz expires_at
        timestamptz created_at
    }
    api_keys {
        uuid id PK
        uuid org_id FK
        text name
        text prefix "first 8 chars"
        text key_hash UK "SHA-256"
        int rpm_limit "stored, not enforced yet"
        bigint monthly_budget_micros "stored, not enforced yet"
        timestamptz revoked_at
        timestamptz last_used_at
        timestamptz created_at
    }
    requests {
        uuid id PK
        uuid request_id UK "idempotency key"
        uuid org_id FK "denormalized"
        uuid api_key_id FK
        text model
        text provider
        smallint status_code
        int prompt_tokens
        int completion_tokens
        bigint cost_micros
        int latency_ms
        bool streamed
        timestamptz created_at
    }
    usage_rollups {
        uuid api_key_id PK, FK
        text model PK
        timestamptz bucket_start PK "on the hour"
        uuid org_id FK
        int request_count
        bigint tokens
        bigint cost_micros
    }
    model_prices {
        text model PK
        timestamptz effective_from PK
        bigint input_micros_per_1k
        bigint output_micros_per_1k
    }
```

The decisions worth defending:

- **Money is integer micros.** Costs and prices are `BIGINT` millionths of a dollar. Floats can't represent most decimal amounts exactly, so summing thousands of small float costs drifts. `NUMERIC` would be exact but slower to sum, and it comes back as `Decimal`, which then has to be handled carefully in both Python and JSON. A micro is fine enough for per-token prices, and `BIGINT` holds about 9.2 trillion dollars. The one rounding step (half up, to a whole micro) happens once per request, in `cost_micros()`.
- **SHA-256 for keys and session tokens, argon2id for passwords.** Keys and tokens are 256 random bits, so a fast hash can't be brute-forced, and a deterministic hash lets each request find its key through a unique index in one lookup. argon2 salts every hash, so a lookup would mean scanning and verifying every key. Passwords are low-entropy and chosen by people, so they get a deliberately slow, memory-hard hash. Login for an unknown email still runs a dummy verify, so response time doesn't reveal which emails exist.
- **`org_id` is copied onto `requests` and `usage_rollups`.** The request log and usage reports filter by org on every call. With `org_id` on the row, they read the `(org_id, created_at DESC)` index directly instead of joining through `api_keys`. Rollups use the same idea.
- **A composite foreign key keeps that copy honest.** `(api_key_id, org_id)` references `api_keys (id, org_id)` through a unique constraint that exists only for this purpose. The database rejects a request row that names one org's key under another org, so a bug in the write path can't leak one tenant's traffic into another's reports. Tests insert such rows and assert that they fail.

Full column notes, indexes, and constraints are in [docs/DATABASE.md](docs/DATABASE.md).

## Testing

292 tests: 229 backend (pytest) and 63 frontend (Vitest). The backend tests run against real Postgres, not SQLite or mocks, because the behavior worth testing lives in Postgres: composite foreign keys, upserts, row locks, `citext`, and time zones.

| Level | What it covers | Why at this level |
| --- | --- | --- |
| Unit | SSE usage parsing across chunk boundaries, cost rounding, settings validation, error shapes | Pure logic with many edge cases, cheap to enumerate |
| Database | Constraints reject cross-org rows, rollup upserts increment, hour-bucket CHECK ignores session time zone, cascades, indexes exist | The invariants are enforced by the schema, so the schema is what gets tested |
| API | Every management route through the full app over ASGI: auth, 404 vs 403 per role, last-owner rule, pagination walks, cursor tampering, usage bucketing | Exercises dependency wiring, SQL, and serialization together |
| Proxy | Pass-through, 502/504 mapping, streaming relay, mid-stream failure, client disconnect cancelling upstream, usage recorded after a stream, recording failures not breaking responses | Uses `httpx.MockTransport` to produce upstream failures on demand |
| Integration | Gate in process against the real mock provider app, streaming and not | Checks the two apps agree on the wire format |
| Contract | Migrations round-trip with no diff from the models. Every route documents its errors in OpenAPI. | Stops schema and API docs drifting from code |
| Frontend | Each page's loading, empty, error, and success states, forms, role-based controls, cursor paging, session expiry redirect | Runs the real API client against MSW |

Each xdist worker gets its own migrated database. Database tests run inside a transaction that is rolled back afterwards. API tests commit for real, as the app does, and empty the tables around each test. More in [docs/TESTING.md](docs/TESTING.md).

## Built and planned

**Built and working today**

- OpenAI-compatible `POST /v1/chat/completions` proxy, with streaming (SSE relayed chunk by chunk) and without
- Two auth schemes: bearer API keys for the proxy, httpOnly session cookies for the dashboard and management API
- Organizations with owner, admin, and member roles. Non-members get 404, not 403, so org IDs can't be probed.
- API key management: create (the key is shown once and stored hashed), list, and revoke. Revocation takes effect on the next request because keys are checked on every call without a cache.
- Synchronous usage recording: every proxied request, including upstream failures and requests rejected for a revoked key, is written with tokens, cost, latency, and status before the response completes
- Request log with filters and HMAC-signed keyset pagination, and usage reports by hour or day, grouped by model or key
- React dashboard: landing page, sign in and registration, spend overview, request log, API keys, members

**Planned, not built**

- Upstream provider credentials, so Gate can forward to OpenAI or another hosted provider (it forwards no provider key today)
- Enforced rate limiting. `rpm_limit` is stored per key but not checked.
- Budget enforcement. `monthly_budget_micros` is stored per key but not checked.
- Async usage pipeline, moving recording off the request path. `request_id` is already a unique idempotency key for this.
- Provider failover
- Response caching
- Docker image for Gate itself
- Kubernetes manifests
- CI/CD

## AI-assisted development

Gate was built with [Claude Code](https://claude.com/claude-code), in a series of small, scoped tasks that each ended in a commit (see the git history).

**What I specified and decided.** The scope and order of each piece of work. The data model decisions in the [Database](#database) section and the records in [docs/DECISIONS.md](docs/DECISIONS.md). The definition of done in [CLAUDE.md](CLAUDE.md): pytest, ruff, and mypy all pass, or nothing gets committed. The dashboard's visual direction, in [DESIGN.md](DESIGN.md). Which features are in and which are deferred.

**What was generated.** Most of the code, tests, migrations, and these docs, from those specifications. I reviewed the diffs, and the rule that every change passes the full test suite, lint, and type check did much of the enforcement.

**What I'd change.** I'd review scaffolding and packaging with the same care as feature code. Generated defaults can outlive the moment they were created in, and a green test suite says nothing about metadata. I'd also run an end-to-end check against a real provider earlier. The mock provider made the proxy easy to test, and a real provider would have shown sooner that Gate forwards no upstream credentials.

## Local development

**Prerequisites:** Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL (tested on 17; the `citext` extension is created by the first migration), and Node 20.19+ or 22.12+ for the dashboard.

**Environment variables** (read from `.env`; copy `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:devpassword@localhost:5432/gate` | App database |
| `TEST_DATABASE_URL` | `...localhost:5432/gate_test` | Base name for test databases. Each xdist worker appends its ID. |
| `UPSTREAM_BASE_URL` | `http://localhost:8001` | Provider to forward to |
| `UPSTREAM_PROVIDER` | `mock` | Name recorded as each request's provider |
| `SECRET_KEY` | none, required | Signs pagination cursors. At least 32 characters. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. |
| `DEV` | `false` | Allows running without `SECRET_KEY`, using a fixed, public key. Required by the seed script. Never set in production. |
| `SESSION_TTL` | 14 days | Session lifetime, as seconds or ISO 8601 (`P14D`) |

**Database and seed data:**

```sh
uv run alembic upgrade head
uv run python -m gate.dev_seed
```

The seed script refuses to run unless `DEV` is set. It creates `dev@example.com` / `correct horse battery`, an org with three keys (`production`, `staging`, `ci`), and prices for `mock-1`, `mock-mini`, and `mock-large`. It also generates 500 requests over the past 30 days through the same write path as live traffic. It's safe to rerun, and the traffic is regenerated each time so it always covers the last 30 days.

**Running:**

```sh
uv run uvicorn gate.mock_provider:create_app --factory --port 8001   # provider
uv run uvicorn gate.main:create_app --factory --port 8000 --reload   # Gate
cd frontend && npm install && npm run dev                            # dashboard on :5173
```

The session cookie is marked `Secure`. Browsers accept it over plain HTTP on `localhost`, so local development works without TLS.

**Tests and checks:**

```sh
uv run pytest            # backend, parallel. Skips DB tests if Postgres is down, unless CI is set.
uv run ruff check
uv run mypy src tests
cd frontend && npm run check   # oxlint, tsc, vitest, build
cd frontend && npm run check:api   # fails if generated API types are stale
```

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, request lifecycle, streaming, failure handling
- [docs/API.md](docs/API.md): every endpoint, auth, roles, error formats
- [docs/DATABASE.md](docs/DATABASE.md): tables, constraints, indexes, migrations
- [docs/TESTING.md](docs/TESTING.md): fixtures, test layers, what isn't tested
- [docs/DECISIONS.md](docs/DECISIONS.md): architecture decision records
