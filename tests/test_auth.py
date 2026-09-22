import hashlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gate.auth import SESSION_COOKIE
from gate.models import AuthSession, Membership, Organization, User

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"

MakeClient = Callable[[], httpx.AsyncClient]
Sessionmaker = async_sessionmaker[AsyncSession]


async def register(
    client: httpx.AsyncClient, email: str = "ada@example.com"
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )


async def login(
    client: httpx.AsyncClient, email: str = "ada@example.com", password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def count(sessionmaker: Sessionmaker, model: type[Any]) -> int:
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


# --- registration ---


async def test_register_creates_user_with_personal_org(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker
) -> None:
    client = make_api_client()

    response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert set(body) == {"id", "email", "created_at"}
    async with committed_sessionmaker() as session:
        user = await session.get_one(User, body["id"])
        assert user.password_hash.startswith("$argon2id$")
        [(membership, org)] = (
            await session.execute(select(Membership, Organization).join(Organization))
        ).all()
    assert (membership.user_id, membership.role) == (user.id, "owner")
    assert org.name == "ada@example.com's organization"


@pytest.mark.parametrize("second", ["ada@example.com", "ADA@Example.COM"])
async def test_duplicate_registration_is_rejected(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker, second: str
) -> None:
    client = make_api_client()
    await register(client, "ada@example.com")

    response = await register(client, second)

    assert response.status_code == 409
    assert response.json() == {"detail": "Email already registered"}
    # The failed attempt left no stray org or membership behind.
    assert await count(committed_sessionmaker, User) == 1
    assert await count(committed_sessionmaker, Organization) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "ada@example.com", "password": "short"},
        {"email": "ada@example.com", "password": "x" * 257},
        {"email": "ada@example.com"},
        {"password": PASSWORD},
        {"email": ["ada@example.com"], "password": PASSWORD},
        [],
    ],
    ids=[
        "bad-email",
        "short-password",
        "long-password",
        "no-password",
        "no-email",
        "wrong-type",
        "not-an-object",
    ],
)
async def test_malformed_registration_is_rejected(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker, body: object
) -> None:
    response = await make_api_client().post("/api/v1/auth/register", json=body)

    assert response.status_code == 422
    assert await count(committed_sessionmaker, User) == 0


async def test_non_json_body_is_rejected(make_api_client: MakeClient) -> None:
    response = await make_api_client().post(
        "/api/v1/auth/login",
        content=b"email=ada@example.com",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 422


# --- login ---


async def test_login_sets_a_hardened_cookie_and_stores_only_its_hash(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker
) -> None:
    client = make_api_client()
    await register(client)

    response = await login(client)

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "secure", "samesite=lax", "path=/"} <= attributes
    assert "max-age=1209600" in attributes

    token = response.cookies[SESSION_COOKIE]
    async with committed_sessionmaker() as session:
        [stored] = (await session.scalars(select(AuthSession.token_hash))).all()
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored


async def test_login_email_is_case_insensitive(make_api_client: MakeClient) -> None:
    client = make_api_client()
    await register(client, "ada@example.com")

    assert (await login(client, "Ada@EXAMPLE.com")).status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [("ada@example.com", "wrong password"), ("nobody@example.com", PASSWORD)],
    ids=["wrong-password", "unknown-email"],
)
async def test_invalid_login_is_rejected_without_a_session(
    make_api_client: MakeClient,
    committed_sessionmaker: Sessionmaker,
    email: str,
    password: str,
) -> None:
    client = make_api_client()
    await register(client)

    response = await login(client, email, password)

    # The same answer either way, so login can't be used to find accounts.
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert "set-cookie" not in response.headers
    assert await count(committed_sessionmaker, AuthSession) == 0


async def test_each_login_gets_its_own_session(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker
) -> None:
    client = make_api_client()
    await register(client)

    first = (await login(client)).cookies[SESSION_COOKIE]
    second = (await login(client)).cookies[SESSION_COOKIE]

    assert first != second
    assert await count(committed_sessionmaker, AuthSession) == 2


# --- the session cookie ---


async def test_me_returns_user_and_orgs(make_api_client: MakeClient) -> None:
    client = make_api_client()
    user_id = (await register(client)).json()["id"]
    await login(client)

    response = await client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["email"]) == (user_id, "ada@example.com")
    [org] = body["orgs"]
    assert org["role"] == "owner"
    assert org["name"] == "ada@example.com's organization"


async def test_missing_cookie_is_unauthorized(make_api_client: MakeClient) -> None:
    response = await make_api_client().get("/api/v1/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def with_cookie(token: str) -> dict[str, str]:
    # Sent as a header rather than via the cookie jar, so a test can't pass just
    # because the jar quietly didn't send it.
    return {"cookie": f"{SESSION_COOKIE}={token}"}


async def test_unknown_session_token_is_unauthorized(
    make_api_client: MakeClient,
) -> None:
    response = await make_api_client().get("/api/v1/me", headers=with_cookie("x"))

    assert response.status_code == 401


async def test_expired_session_is_unauthorized(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker
) -> None:
    client = make_api_client()
    await register(client)
    await login(client)
    assert (await client.get("/api/v1/me")).status_code == 200

    async with committed_sessionmaker() as session:
        await session.execute(
            update(AuthSession).values(
                expires_at=func.now() - text("interval '1 second'")
            )
        )
        await session.commit()

    assert (await client.get("/api/v1/me")).status_code == 401


async def test_logout_deletes_the_session(
    make_api_client: MakeClient, committed_sessionmaker: Sessionmaker
) -> None:
    client, replay = make_api_client(), make_api_client()
    await register(client)
    token = (await login(client)).cookies[SESSION_COOKIE]
    assert (await replay.get("/api/v1/me", headers=with_cookie(token))).is_success

    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert await count(committed_sessionmaker, AuthSession) == 0
    assert SESSION_COOKIE not in client.cookies
    # A client that kept a copy of the cookie is logged out too.
    assert (
        await replay.get("/api/v1/me", headers=with_cookie(token))
    ).status_code == 401


async def test_logout_only_ends_its_own_session(
    make_api_client: MakeClient,
) -> None:
    laptop, phone = make_api_client(), make_api_client()
    await register(laptop)
    await login(laptop)
    await login(phone)

    await laptop.post("/api/v1/auth/logout")

    assert (await laptop.get("/api/v1/me")).status_code == 401
    assert (await phone.get("/api/v1/me")).status_code == 200


async def test_logout_without_a_session_succeeds(
    make_api_client: MakeClient,
) -> None:
    assert (await make_api_client().post("/api/v1/auth/logout")).status_code == 204
