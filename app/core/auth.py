from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Header

from app.core.errors import UnauthorizedError
from app.core.security import bearer_token, keys_match


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    name: str


def make_admin_auth(admin_key: str) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency: only `Authorization: Bearer <ADMIN_API_KEY>` gets through."""

    async def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        token = bearer_token(authorization)
        if not token or not keys_match(token, admin_key):
            raise UnauthorizedError("Invalid admin key")

    return require_admin


ResolveUser = Callable[[str], Awaitable[CurrentUser | None]]


def make_user_auth(resolve: ResolveUser) -> Callable[..., Awaitable[CurrentUser]]:
    """FastAPI dependency: the user whose API key is in the bearer header. `resolve` is
    supplied by the users module, so this file does not depend on any module."""

    async def current_user(authorization: Annotated[str | None, Header()] = None) -> CurrentUser:
        token = bearer_token(authorization)
        user = await resolve(token) if token else None
        if user is None:
            raise UnauthorizedError("Invalid API key")
        return user

    return current_user
