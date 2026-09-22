import hashlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import Account, Signup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gate.models import ApiKey

pytestmark = pytest.mark.anyio

MakeClient = Callable[[], httpx.AsyncClient]
Sessionmaker = async_sessionmaker[AsyncSession]


async def join(owner: Account, member: Account, role: str) -> None:
    response = await owner.client.post(
        owner.org("/members"), json={"email": member.email, "role": role}
    )
    assert response.status_code == 201, response.text


async def create_key(
    account: Account, org_path: str, **fields: object
) -> dict[str, Any]:
    response = await account.client.post(
        org_path + "/keys", json={"name": "ci", **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- isolation between orgs ---


async def test_other_orgs_are_indistinguishable_from_missing_ones(
    signup: Signup,
) -> None:
    ada, eve = await signup("ada"), await signup("eve")
    missing = "/api/v1/orgs/00000000-0000-7000-8000-000000000000"
    key = await create_key(ada, ada.org())

    requests = [
        ("GET", ""),
        ("GET", "/members"),
        ("POST", "/members"),
        ("PATCH", f"/members/{ada.user_id}"),
        ("DELETE", f"/members/{ada.user_id}"),
        ("GET", "/keys"),
        ("POST", "/keys"),
        ("DELETE", f"/keys/{key['id']}"),
    ]
    bodies = {
        "POST /members": {"email": eve.email, "role": "owner"},
        "PATCH": {"role": "member"},
        "POST /keys": {"name": "stolen"},
    }
    for method, path in requests:
        body = bodies.get(f"{method} {path}") or bodies.get(method)
        other = await eve.client.request(method, ada.org(path), json=body)
        absent = await eve.client.request(method, missing + path, json=body)

        assert other.status_code == 404, (method, path)
        assert other.json() == absent.json() == {"detail": "Organization not found"}

    # Nothing changed in ada's org.
    members = (await ada.client.get(ada.org("/members"))).json()
    assert [(m["email"], m["role"]) for m in members] == [(ada.email, "owner")]
    [listed] = (await ada.client.get(ada.org("/keys"))).json()
    assert listed["revoked_at"] is None


async def test_own_org_path_cannot_reach_another_orgs_key(signup: Signup) -> None:
    ada, eve = await signup("ada"), await signup("eve")
    key = await create_key(ada, ada.org())

    # eve is an owner of her own org, so authorization passes; the key lookup
    # itself has to be scoped to that org.
    response = await eve.client.delete(eve.org(f"/keys/{key['id']}"))

    assert response.status_code == 404
    assert response.json() == {"detail": "API key not found"}
    [listed] = (await ada.client.get(ada.org("/keys"))).json()
    assert listed["revoked_at"] is None


async def test_org_routes_need_a_session(make_api_client: MakeClient) -> None:
    response = await make_api_client().get(
        "/api/v1/orgs/00000000-0000-7000-8000-000000000000/keys"
    )

    assert response.status_code == 401


# --- roles ---


async def test_member_is_read_only(signup: Signup) -> None:
    ada, bob, eve = await signup("ada"), await signup("bob"), await signup("eve")
    await join(ada, bob, "member")
    key = await create_key(ada, ada.org())

    assert (await bob.client.get(ada.org())).json()["role"] == "member"
    assert (await bob.client.get(ada.org("/members"))).status_code == 200
    assert (await bob.client.get(ada.org("/keys"))).status_code == 200

    writes = [
        bob.client.post(ada.org("/keys"), json={"name": "mine"}),
        bob.client.delete(ada.org(f"/keys/{key['id']}")),
        bob.client.post(
            ada.org("/members"), json={"email": eve.email, "role": "member"}
        ),
        bob.client.patch(ada.org(f"/members/{bob.user_id}"), json={"role": "owner"}),
        bob.client.delete(ada.org(f"/members/{ada.user_id}")),
    ]
    for write in writes:
        response = await write
        assert response.status_code == 403
        assert response.json() == {"detail": "Insufficient role"}


async def test_admin_manages_keys_but_not_members(signup: Signup) -> None:
    ada, bob, eve = await signup("ada"), await signup("bob"), await signup("eve")
    await join(ada, bob, "admin")

    key = await create_key(bob, ada.org())
    assert (await bob.client.delete(ada.org(f"/keys/{key['id']}"))).status_code == 204

    response = await bob.client.post(
        ada.org("/members"), json={"email": eve.email, "role": "member"}
    )
    assert response.status_code == 403
    response = await bob.client.patch(
        ada.org(f"/members/{bob.user_id}"), json={"role": "owner"}
    )
    assert response.status_code == 403


async def test_owner_manages_members(signup: Signup) -> None:
    ada, bob = await signup("ada"), await signup("bob")

    await join(ada, bob, "member")
    response = await ada.client.patch(
        ada.org(f"/members/{bob.user_id}"), json={"role": "admin"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert (await bob.client.get(ada.org())).json()["role"] == "admin"

    response = await ada.client.delete(ada.org(f"/members/{bob.user_id}"))
    assert response.status_code == 204
    assert (await bob.client.get(ada.org())).status_code == 404


@pytest.mark.parametrize("who", ["self", "other"])
async def test_adding_an_existing_member_conflicts(signup: Signup, who: str) -> None:
    ada, bob = await signup("ada"), await signup("bob")
    await join(ada, bob, "member")
    email = ada.email if who == "self" else bob.email

    response = await ada.client.post(
        ada.org("/members"), json={"email": email, "role": "admin"}
    )

    assert response.status_code == 409


async def test_adding_an_unknown_user_is_not_found(signup: Signup) -> None:
    ada = await signup("ada")

    response = await ada.client.post(
        ada.org("/members"), json={"email": "nobody@example.com", "role": "member"}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


async def test_last_owner_cannot_leave_or_step_down(signup: Signup) -> None:
    ada, bob = await signup("ada"), await signup("bob")
    await join(ada, bob, "admin")

    demote = await ada.client.patch(
        ada.org(f"/members/{ada.user_id}"), json={"role": "admin"}
    )
    leave = await ada.client.delete(ada.org(f"/members/{ada.user_id}"))
    assert demote.status_code == leave.status_code == 409

    # With a second owner, either change is fine.
    await ada.client.patch(ada.org(f"/members/{bob.user_id}"), json={"role": "owner"})
    assert (
        await ada.client.delete(ada.org(f"/members/{ada.user_id}"))
    ).status_code == 204
    assert (await bob.client.get(ada.org("/members"))).json()[0]["email"] == bob.email


# --- API keys ---


async def test_created_key_is_shown_once_and_stored_hashed(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> None:
    ada = await signup("ada")

    created = await create_key(ada, ada.org(), rpm_limit=120)

    plaintext = created["key"]
    assert plaintext.startswith("gk_")
    assert len(plaintext) == len("gk_") + 43  # 32 bytes, base64url
    assert created["prefix"] == plaintext[:8]
    assert created["rpm_limit"] == 120
    assert created["monthly_budget_micros"] is None

    [listed] = (await ada.client.get(ada.org("/keys"))).json()
    assert "key" not in listed
    assert listed == {k: v for k, v in created.items() if k != "key"}

    async with committed_sessionmaker() as session:
        stored = await session.get_one(ApiKey, created["id"])
    assert stored.key_hash == hashlib.sha256(plaintext.encode()).hexdigest()


async def test_key_gets_default_rpm_limit(signup: Signup) -> None:
    ada = await signup("ada")

    assert (await create_key(ada, ada.org()))["rpm_limit"] == 60


async def test_revoking_keeps_the_key_listed_and_is_idempotent(
    signup: Signup, committed_sessionmaker: Sessionmaker
) -> None:
    ada = await signup("ada")
    key = await create_key(ada, ada.org())

    assert (await ada.client.delete(ada.org(f"/keys/{key['id']}"))).status_code == 204
    [first] = (await ada.client.get(ada.org("/keys"))).json()
    assert (await ada.client.delete(ada.org(f"/keys/{key['id']}"))).status_code == 204
    [second] = (await ada.client.get(ada.org("/keys"))).json()

    assert first["revoked_at"] is not None
    assert second["revoked_at"] == first["revoked_at"]
    # Revoked, not deleted: requests and rollups keep pointing at it.
    async with committed_sessionmaker() as session:
        assert await session.get(ApiKey, key["id"]) is not None


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/orgs/not-a-uuid/keys", None),
        ("POST", "{org}/keys", {"name": ""}),
        ("POST", "{org}/keys", {"name": "k", "rpm_limit": 0}),
        ("POST", "{org}/keys", {"name": "k", "monthly_budget_micros": -1}),
        ("POST", "{org}/keys", {"name": "k", "rpm_limit": 2**31}),
        ("POST", "{org}/members", {"email": "bob@example.com", "role": "god"}),
        ("PATCH", "{org}/members/not-a-uuid", {"role": "admin"}),
        ("DELETE", "{org}/keys/not-a-uuid", None),
    ],
)
async def test_malformed_org_requests_are_rejected(
    signup: Signup, method: str, path: str, body: object
) -> None:
    ada = await signup("ada")

    response = await ada.client.request(method, path.format(org=ada.org()), json=body)

    assert response.status_code == 422
