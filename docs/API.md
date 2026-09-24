# API

[Back to README](../README.md) · [Architecture](ARCHITECTURE.md) · [Database](DATABASE.md) · [Testing](TESTING.md) · [Decisions](DECISIONS.md)

With Gate running, the full OpenAPI document is at http://localhost:8000/openapi.json and interactive docs are at http://localhost:8000/docs. This page summarizes it. The examples assume the [quick start](../README.md#quick-start) setup.

## Authentication

| Route group | Scheme | How to get it |
| --- | --- | --- |
| `/v1/...` (proxy) | `Authorization: Bearer gk_...` | Create a key with `POST /api/v1/orgs/{org_id}/keys` |
| `/api/v1/...` (management) | `gate_session` cookie | `POST /api/v1/auth/login` |

The session cookie is `HttpOnly`, `Secure`, and `SameSite=Lax`, and lasts `SESSION_TTL` (14 days by default). A session cookie doesn't authenticate the proxy, and an API key doesn't authenticate the management API.

## Errors

The proxy uses OpenAI's error envelope, so SDKs raise their usual exceptions:

```json
{"error": {"message": "Invalid API key", "type": "invalid_request_error", "param": null, "code": "invalid_api_key"}}
```

| Status | `type` | When |
| --- | --- | --- |
| 401 | `invalid_request_error` | Missing, unknown, or revoked key (`code: "invalid_api_key"`) |
| 422 | `invalid_request_error` | Body isn't a valid request. `param` names the field. |
| 502 | `upstream_error` | Provider unreachable |
| 504 | `upstream_error` | Provider timed out |
| other | from the provider | The provider's own error, passed through unchanged |

The management API uses FastAPI's format: `{"detail": "Insufficient role"}`, or a list of field errors for 422.

## Proxy

### `POST /v1/chat/completions`

OpenAI's chat completions request. Gate requires `model`, reads `stream`, and forwards the body unchanged.

```sh
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gk_dev_production_000000000000000000000000" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock-1", "messages": [{"role": "user", "content": "Hello through Gate"}]}'
```

Streaming, with usage reported at the end so Gate can record tokens and cost:

```sh
curl -N http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gk_dev_production_000000000000000000000000" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock-1", "stream": true, "stream_options": {"include_usage": true},
       "messages": [{"role": "user", "content": "Hello through Gate"}]}'
```

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1790212021,"model":"mock-1","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1790212021,"model":"mock-1","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1790212021,"model":"mock-1","choices":[{"index":0,"delta":{"content":"through "},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1790212021,"model":"mock-1","choices":[{"index":0,"delta":{"content":"Gate"},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1790212021,"model":"mock-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":null}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1790212021,"model":"mock-1","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":3,"total_tokens":6}}

data: [DONE]
```

This is the only proxied endpoint today.

## Health

| Route | Auth | Returns |
| --- | --- | --- |
| `GET /healthz` | none | `200 {"status": "ok"}`. Doesn't touch the database. |
| `GET /readyz` | none | `200 {"status": "ok", "checks": {"database": "up"}}`, or `503` with `"database": "down"` |

## Accounts and sessions

| Route | Auth | Notes |
| --- | --- | --- |
| `POST /api/v1/auth/register` | none | `{"email", "password"}`, password 8 to 256 characters. Creates the user and a personal org they own. `201`, or `409` if the email is taken. |
| `POST /api/v1/auth/login` | none | Sets the session cookie. `401` for a wrong email or password, with the same message and timing for both. |
| `POST /api/v1/auth/logout` | cookie | Deletes this session only. `204`, even without a session. |
| `GET /api/v1/me` | cookie | The user and their orgs with their role in each |

```sh
curl -c cookies.txt http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@example.com", "password": "correct horse battery"}'

curl -b cookies.txt http://localhost:8000/api/v1/me
```

```json
{"id":"751889a1-...","email":"dev@example.com","created_at":"...","orgs":[{"id":"3c7f4b1a-...","name":"Acme (dev)","role":"owner"}]}
```

## Organizations and members

Roles, lowest to highest: `member`, `admin`, `owner`. Each route needs at least the role shown. Non-members get `404` for every org route. Members below the required role get `403`.

| Route | Minimum role | Notes |
| --- | --- | --- |
| `GET /api/v1/orgs/{org_id}` | member | Name and the caller's role |
| `GET /api/v1/orgs/{org_id}/members` | member | |
| `POST /api/v1/orgs/{org_id}/members` | owner | `{"email", "role"}`. The user must already have an account (`404` otherwise). `409` if already a member. |
| `PATCH /api/v1/orgs/{org_id}/members/{user_id}` | owner | `{"role"}`. `409` if it would leave no owner. |
| `DELETE /api/v1/orgs/{org_id}/members/{user_id}` | owner | `409` if it would leave no owner |

## API keys

| Route | Minimum role | Notes |
| --- | --- | --- |
| `GET /api/v1/orgs/{org_id}/keys` | member | All keys, including revoked ones. Never includes the key itself. |
| `POST /api/v1/orgs/{org_id}/keys` | admin | `{"name", "rpm_limit"?, "monthly_budget_micros"?}`. Returns the full key once, in `key`. |
| `DELETE /api/v1/orgs/{org_id}/keys/{key_id}` | admin | Revokes the key (sets `revoked_at`). Idempotent. The row is kept so history still references it. |

`rpm_limit` (default 60) and `monthly_budget_micros` are stored and returned, but not enforced yet.

```sh
curl -b cookies.txt -X POST "http://localhost:8000/api/v1/orgs/$ORG_ID/keys" \
  -H "Content-Type: application/json" -d '{"name": "my-service"}'
```

```json
{"id":"01a0d0ef-...","name":"my-service","prefix":"gk_GvvB8","rpm_limit":60,"monthly_budget_micros":null,"revoked_at":null,"last_used_at":null,"created_at":"...","key":"gk_GvvB82bZ..."}
```

```sh
curl -b cookies.txt -X DELETE "http://localhost:8000/api/v1/orgs/$ORG_ID/keys/$KEY_ID"
# The next proxy request with that key gets 401 invalid_api_key.
```

## Reporting

### `GET /api/v1/orgs/{org_id}/requests`

Minimum role: member. The org's requests, newest first.

| Parameter | Type | Notes |
| --- | --- | --- |
| `model` | string | Exact match |
| `status_code` | int, 100 to 599 | |
| `api_key_id` | uuid | |
| `from` | datetime with offset | Inclusive |
| `to` | datetime with offset | Exclusive |
| `limit` | int, 1 to 200 | Default 50 |
| `cursor` | string | `next_cursor` from the previous page |

```sh
curl -b cookies.txt "http://localhost:8000/api/v1/orgs/$ORG_ID/requests?status_code=502&limit=1"
```

```json
{"data":[{"id":"01a0d0ef-...","request_id":"01a0cb39-...","api_key_id":"d877a96e-...","model":"mock-1","provider":"mock","status_code":502,"prompt_tokens":0,"completion_tokens":0,"cost_micros":0,"latency_ms":99,"streamed":false,"created_at":"2026-09-22T22:25:36.143335Z"}],"next_cursor":"eyJ0IjoiMjAyNi0wOS0y..."}
```

Pagination is keyset-based on `(created_at, id)`, so rows arriving while you page don't shift or repeat results. Cursors are signed with `SECRET_KEY` and bound to the org and filters that produced them. A cursor that was edited, or reused with different filters, gets `400 Invalid cursor`. Changing `limit` between pages is allowed.

### `GET /api/v1/orgs/{org_id}/usage`

Minimum role: member. Totals from the hourly rollups.

| Parameter | Type | Notes |
| --- | --- | --- |
| `from` | datetime with offset, required | Inclusive |
| `to` | datetime with offset, required | Exclusive |
| `bucket` | `hour` or `day` | Default `hour`. Days are UTC days. |
| `group_by` | `model` or `key` | Optional |

```sh
curl -b cookies.txt "http://localhost:8000/api/v1/orgs/$ORG_ID/usage?from=2026-09-01T00:00:00Z&to=2026-10-01T00:00:00Z&bucket=day&group_by=model"
```

```json
{"bucket":"day","group_by":"model","data":[{"bucket_start":"2026-09-01T00:00:00Z","model":"mock-1","api_key_id":null,"request_count":11,"tokens":15342,"cost_micros":12328}]}
```

A rollup counts toward the range when its hour starts inside it. Buckets with no usage are left out. `cost_micros` is millionths of a dollar: 12328 is $0.012328.
