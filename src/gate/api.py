"""The management API: accounts, sessions, org members and API keys."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from gate.auth import (
    SESSION_COOKIE,
    CurrentUser,
    DbSession,
    OrgAdmin,
    OrgMember,
    OrgOwner,
    get_settings,
    session_cookie,
)
from gate.config import Settings
from gate.models import ApiKey, AuthSession, Membership, Organization, Role, User
from gate.security import (
    API_KEY_DISPLAY_LENGTH,
    generate_api_key,
    generate_session_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    spend_password_check_time,
    verify_password,
)

INT4_MAX = 2**31 - 1
INT8_MAX = 2**63 - 1
# Bounds the argon2 work an anonymous request can ask for.
PASSWORD_MAX_LENGTH = 256

router = APIRouter(prefix="/api/v1", tags=["management"])


class ErrorDetail(BaseModel):
    """FastAPI's error body, used across the management API."""

    detail: str


def error(description: str) -> dict[str, Any]:
    return {"model": ErrorDetail, "description": description}


Responses = dict[int | str, dict[str, Any]]

# Documented error responses, composed per route. 422 is added automatically.
NOT_LOGGED_IN: Responses = {401: error("No valid session cookie")}
IN_ORG: Responses = {
    **NOT_LOGGED_IN,
    404: error("Not a member of the organization, or it doesn't exist"),
}
WITH_ROLE: Responses = {
    **IN_ORG,
    403: error("A member, but without the required role"),
}


# --- schemas ---


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=PASSWORD_MAX_LENGTH)


class LoginRequest(BaseModel):
    email: EmailStr
    # No minimum: length rules apply when a password is chosen, not when it's
    # checked.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    role: Role


class MeOut(UserOut):
    orgs: list[OrgOut]


class MemberIn(BaseModel):
    email: EmailStr
    role: Role


class RoleUpdate(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: Role
    created_at: datetime


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rpm_limit: int | None = Field(default=None, ge=1, le=INT4_MAX)
    monthly_budget_micros: int | None = Field(default=None, ge=0, le=INT8_MAX)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    rpm_limit: int
    monthly_budget_micros: int | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class CreatedApiKeyOut(ApiKeyOut):
    key: str = Field(description="The full key. It is shown only this once.")


# --- accounts and sessions ---


def conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    responses={409: error("Email already registered")},
)
async def register(body: RegisterRequest, session: DbSession) -> UserOut:
    # Hash before touching the database so no transaction is held meanwhile.
    password_hash = await hash_password(body.password)
    user = User(email=body.email, password_hash=password_hash)
    organization = Organization(name=f"{body.email}'s organization")
    session.add(Membership(user=user, organization=organization, role="owner"))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_users_email" in str(exc.orig):
            raise conflict("Email already registered") from exc
        raise
    return UserOut.model_validate(user)


@router.post("/auth/login", responses={401: error("Invalid email or password")})
async def login(
    body: LoginRequest,
    response: Response,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None:
        await spend_password_check_time(body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not await verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password(body.password)

    token = generate_session_token()
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=func.now() + settings.session_ttl,
        )
    )
    await session.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(settings.session_ttl.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return UserOut.model_validate(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: DbSession,
    token: Annotated[str | None, Depends(session_cookie)],
) -> None:
    # Idempotent: logging out without a live session still clears the cookie.
    if token is not None:
        await session.execute(
            delete(AuthSession).where(AuthSession.token_hash == hash_token(token))
        )
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=True, samesite="lax")


@router.get("/me", responses=NOT_LOGGED_IN)
async def me(user: CurrentUser, session: DbSession) -> MeOut:
    rows = await session.execute(
        select(Organization, Membership.role)
        .join(Membership)
        .where(Membership.user_id == user.id)
        .order_by(Organization.created_at, Organization.id)
    )
    return MeOut(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        orgs=[OrgOut(id=org.id, name=org.name, role=role) for org, role in rows],
    )


# --- organizations ---
#
# Every route below resolves the caller's membership first (OrgMember etc.),
# so a non-member gets 404 before anything about the org is looked up.


@router.get("/orgs/{org_id}", responses=IN_ORG)
async def get_org(membership: OrgMember, session: DbSession) -> OrgOut:
    org = await session.get_one(Organization, membership.org_id)
    return OrgOut(id=org.id, name=org.name, role=membership.role)


@router.get("/orgs/{org_id}/members", responses=IN_ORG)
async def list_members(membership: OrgMember, session: DbSession) -> list[MemberOut]:
    rows = await session.execute(
        select(Membership, User.email)
        .join(User)
        .where(Membership.org_id == membership.org_id)
        .order_by(Membership.created_at, Membership.user_id)
    )
    return [
        MemberOut(
            user_id=member.user_id,
            email=email,
            role=member.role,
            created_at=member.created_at,
        )
        for member, email in rows
    ]


@router.post(
    "/orgs/{org_id}/members",
    status_code=status.HTTP_201_CREATED,
    responses={
        **WITH_ROLE,
        404: error("No such organization for this user, or no user with that email"),
        409: error("User is already a member"),
    },
)
async def add_member(
    body: MemberIn, membership: OrgOwner, session: DbSession
) -> MemberOut:
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise not_found("User")
    # Checked up front as well as by the constraint below: re-adding a member
    # already in this session (e.g. the owner themselves) would otherwise fail
    # in the ORM's identity map before reaching the database.
    if await session.get(Membership, (user.id, membership.org_id)) is not None:
        raise conflict("User is already a member")
    member = Membership(user_id=user.id, org_id=membership.org_id, role=body.role)
    session.add(member)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "pk_memberships" in str(exc.orig):
            raise conflict("User is already a member") from exc
        raise
    return MemberOut(
        user_id=user.id,
        email=user.email,
        role=member.role,
        created_at=member.created_at,
    )


async def lock_owner_ids(session: DbSession, org_id: uuid.UUID) -> set[uuid.UUID]:
    """The org's owners, row-locked until commit so two concurrent demotions
    can't each see the other as the remaining owner."""
    owners = await session.scalars(
        select(Membership.user_id)
        .where(Membership.org_id == org_id, Membership.role == "owner")
        .with_for_update()
    )
    return set(owners)


async def get_member(
    session: DbSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership:
    member = await session.get(Membership, (user_id, org_id))
    if member is None:
        raise not_found("Member")
    return member


@router.patch(
    "/orgs/{org_id}/members/{user_id}",
    responses={
        **WITH_ROLE,
        404: error("No such organization for this user, or no such member"),
        409: error("It would leave the organization without an owner"),
    },
)
async def update_member_role(
    user_id: uuid.UUID, body: RoleUpdate, membership: OrgOwner, session: DbSession
) -> MemberOut:
    owners = await lock_owner_ids(session, membership.org_id)
    member = await get_member(session, membership.org_id, user_id)
    if body.role != "owner" and owners == {user_id}:
        raise conflict("An organization must keep at least one owner")
    member.role = body.role
    await session.commit()
    user = await session.get_one(User, user_id)
    return MemberOut(
        user_id=user_id,
        email=user.email,
        role=member.role,
        created_at=member.created_at,
    )


@router.delete(
    "/orgs/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **WITH_ROLE,
        404: error("No such organization for this user, or no such member"),
        409: error("It would leave the organization without an owner"),
    },
)
async def remove_member(
    user_id: uuid.UUID, membership: OrgOwner, session: DbSession
) -> None:
    owners = await lock_owner_ids(session, membership.org_id)
    member = await get_member(session, membership.org_id, user_id)
    if owners == {user_id}:
        raise conflict("An organization must keep at least one owner")
    await session.delete(member)
    await session.commit()


# --- API keys ---


@router.get("/orgs/{org_id}/keys", responses=IN_ORG)
async def list_keys(membership: OrgMember, session: DbSession) -> list[ApiKeyOut]:
    keys = await session.scalars(
        select(ApiKey)
        .where(ApiKey.org_id == membership.org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    )
    return [ApiKeyOut.model_validate(key) for key in keys]


@router.post(
    "/orgs/{org_id}/keys", status_code=status.HTTP_201_CREATED, responses=WITH_ROLE
)
async def create_key(
    body: ApiKeyIn, membership: OrgAdmin, session: DbSession
) -> CreatedApiKeyOut:
    plaintext = generate_api_key()
    key = ApiKey(
        org_id=membership.org_id,
        name=body.name,
        prefix=plaintext[:API_KEY_DISPLAY_LENGTH],
        key_hash=hash_token(plaintext),
        monthly_budget_micros=body.monthly_budget_micros,
    )
    if body.rpm_limit is not None:
        key.rpm_limit = body.rpm_limit
    session.add(key)
    await session.commit()
    return CreatedApiKeyOut(**ApiKeyOut.model_validate(key).model_dump(), key=plaintext)


@router.delete(
    "/orgs/{org_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **WITH_ROLE,
        404: error("No such organization for this user, or no such key in it"),
    },
)
async def revoke_key(
    key_id: uuid.UUID, membership: OrgAdmin, session: DbSession
) -> None:
    # Scoped to the path's org, so another org's key id is simply not found.
    key = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == membership.org_id)
    )
    if key is None:
        raise not_found("API key")
    # Revoking is kept rather than deleting: requests and rollups reference the
    # key, and revoking twice keeps the original timestamp.
    await session.execute(
        update(ApiKey)
        .where(ApiKey.id == key.id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await session.commit()
