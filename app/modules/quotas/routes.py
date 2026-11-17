from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.auth import CurrentUser
from app.modules.quotas.controller import QuotasController
from app.modules.quotas.schemas import SetQuotaRequest


def quotas_router(
    controller: QuotasController,
    current_user: Callable[..., Any],
    require_admin: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/quotas", tags=["quotas"])

    @router.get("/me", summary="My storage quota and usage")
    async def me(user: Annotated[CurrentUser, Depends(current_user)]) -> dict[str, Any]:
        return await controller.me(user)

    @router.put("/{user_id}", summary="Set a user's quota", dependencies=[Depends(require_admin)])
    async def set_limit(user_id: UUID, body: SetQuotaRequest) -> dict[str, Any]:
        return await controller.set_limit(user_id, body)

    return router
