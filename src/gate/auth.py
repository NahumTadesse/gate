"""FastAPI dependencies for the two auth schemes and org authorization.

Humans authenticate with a session cookie; programs calling the proxy use a
Bearer API key. Both tokens are opaque and only their SHA-256 is stored.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gate.config import Settings
from gate.db import get_session
from gate.errors import ApiKeyError
from gate.models import ApiKey, AuthSession, Membership, Role, User
from gate.security import hash_token
from gate.usage import UsageRecorder, get_usage_recorder, requested_model

SESSION_COOKIE = "gate_session"

ROLE_RANK: dict[Role, int] = {"member": 0, "admin": 1, "owner": 2}

session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)
bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_current_user(
    session: DbSession,
    token: Annotated[str | None, Depends(session_cookie)],
) -> User:
    """The user behind the session cookie; 401 if it's missing, unknown or
    expired."""
    user = None
    if token is not None:
        user = await session.scalar(
            select(User)
            .join(AuthSession)
            .where(
                AuthSession.token_hash == hash_token(token),
                AuthSession.expires_at > func.now(),
            )
        )
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
) -> ApiKey:
    """The active API key from the Authorization header.

    A revoked key's rejection is recorded against that key. Missing and unknown
    keys can't be: a recorded request must name a key and its org.
    """
    if credentials is None:
        raise ApiKeyError("Missing API key; send it as 'Authorization: Bearer ...'")
    # A short-lived session of its own rather than get_session: that one stays
    # open until the response finishes, which for a stream would hold a pooled
    # connection idle in a transaction for the whole stream.
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        key = await session.scalar(
            select(ApiKey).where(ApiKey.key_hash == hash_token(credentials.credentials))
        )
    if key is None:
        raise ApiKeyError("Invalid API key")
    if key.revoked_at is not None:
        await recorder.record(
            key=key,
            model=requested_model(await request.body()),
            status_code=status.HTTP_401_UNAUTHORIZED,
            usage=None,
            streamed=False,
        )
        raise ApiKeyError("Invalid API key")
    return key


def require_role(minimum: Role) -> Callable[..., Awaitable[Membership]]:
    """A dependency giving the user's membership in the path's org_id.

    Non-members get 404 rather than 403, so org ids can't be probed for
    existence. Members below the required role get 403.
    """

    async def dependency(
        org_id: uuid.UUID, user: CurrentUser, session: DbSession
    ) -> Membership:
        membership = await session.get(Membership, (user.id, org_id))
        if membership is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
        if ROLE_RANK[membership.role] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return membership

    return dependency


OrgMember = Annotated[Membership, Depends(require_role("member"))]
OrgAdmin = Annotated[Membership, Depends(require_role("admin"))]
OrgOwner = Annotated[Membership, Depends(require_role("owner"))]
