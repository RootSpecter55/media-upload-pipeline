from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.users.controller import UsersController
from app.modules.users.schemas import CreateUserRequest


def users_router(controller: UsersController, require_admin: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])

    @router.post("", status_code=201, summary="Create a user; the API key is returned once")
    async def create(body: CreateUserRequest) -> dict[str, Any]:
        return await controller.create(body)

    return router
