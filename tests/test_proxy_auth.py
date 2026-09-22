"""API key authentication on the proxy, against real keys in the database."""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gate.config import Settings
from gate.main import create_app
from gate.models import ApiKey, Organization
from gate.security import hash_token

pytestmark = pytest.mark.anyio

URL = "/v1/chat/completions"
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]}
CREDENTIALS = {"email": "ada@example.com", "password": "correct horse battery"}

MakeClient = Callable[[], httpx.AsyncClient]


@pytest.fixture
async def owner(make_api_client: MakeClient) -> tuple[httpx.AsyncClient, str]:
    """A logged-in owner and their org's keys URL."""
    client = make_api_client()
    await client.post("/api/v1/auth/register", json=CREDENTIALS)
    await client.post("/api/v1/auth/login", json=CREDENTIALS)
    [org] = (await client.get("/api/v1/me")).json()["orgs"]
    return client, f"/api/v1/orgs/{org['id']}/keys"


async def new_key(owner: tuple[httpx.AsyncClient, str]) -> tuple[str, str]:
    client, keys_url = owner
    created = (await client.post(keys_url, json={"name": "ci"})).json()
    return created["id"], created["key"]


def bearer(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


def assert_invalid_key(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    error = response.json()["error"]
    assert (error["type"], error["code"]) == (
        "invalid_request_error",
        "invalid_api_key",
    )


async def test_valid_key_is_proxied(
    make_api_client: MakeClient,
    owner: tuple[httpx.AsyncClient, str],
    upstream_requests: list[httpx.Request],
) -> None:
    _, key = await new_key(owner)

    response = await make_api_client().post(URL, json=BODY, headers=bearer(key))

    assert response.status_code == 200
    assert response.json() == {"id": "chatcmpl-test"}
    assert len(upstream_requests) == 1
    # Gate's key is for Gate; it must not leak to the provider.
    assert "authorization" not in upstream_requests[0].headers


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer gk_unknown"},
        {"authorization": "Bearer "},
        {"authorization": "Basic Z2s6eA=="},
    ],
    ids=["missing", "unknown", "empty", "wrong-scheme"],
)
async def test_bad_credentials_are_rejected_before_upstream(
    make_api_client: MakeClient,
    upstream_requests: list[httpx.Request],
    headers: dict[str, str],
) -> None:
    response = await make_api_client().post(URL, json=BODY, headers=headers)

    assert_invalid_key(response)
    assert upstream_requests == []


async def test_revoked_key_is_rejected(
    make_api_client: MakeClient,
    owner: tuple[httpx.AsyncClient, str],
    upstream_requests: list[httpx.Request],
) -> None:
    key_id, key = await new_key(owner)
    proxy = make_api_client()
    assert (await proxy.post(URL, json=BODY, headers=bearer(key))).status_code == 200

    client, keys_url = owner
    assert (await client.delete(f"{keys_url}/{key_id}")).status_code == 204

    assert_invalid_key(await proxy.post(URL, json=BODY, headers=bearer(key)))
    assert len(upstream_requests) == 1


async def test_session_cookie_does_not_authenticate_the_proxy(
    owner: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = owner  # logged in, but holding no API key

    assert_invalid_key(await client.post(URL, json=BODY))


async def test_auth_is_checked_before_the_body(make_api_client: MakeClient) -> None:
    # An unauthenticated caller learns nothing about what a valid body looks like.
    response = await make_api_client().post(URL, json={"nonsense": True})

    assert_invalid_key(response)


async def test_streaming_does_not_hold_a_database_connection(
    test_database_url: str,
    committed_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    key = "gk_" + "k" * 43
    async with committed_sessionmaker() as session:
        session.add(
            ApiKey(
                organization=Organization(name="Acme"),
                name="ci",
                prefix=key[:8],
                key_hash=hash_token(key),
            )
        )
        await session.commit()

    checked_out: list[int] = []

    class ProbeStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"id": "1"}\n\n'
            # The first chunk has been relayed, so the response is mid-stream.
            checked_out.append(app.state.db_engine.pool.checkedout())
            yield b"data: [DONE]\n\n"

    app = create_app(
        settings=Settings(
            database_url=test_database_url, upstream_base_url="http://upstream.test"
        ),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=ProbeStream()
            )
        ),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client,
    ):
        response = await client.post(
            URL, json={**BODY, "stream": True}, headers=bearer(key)
        )

    assert response.status_code == 200
    assert checked_out == [0]
