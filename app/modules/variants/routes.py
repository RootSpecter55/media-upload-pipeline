from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.auth import CurrentUser
from app.core.pagination import PageParams, page_params
from app.modules.variants.controller import VariantsController


def variants_router(controller: VariantsController, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["variants"])

    @router.get("/assets/{asset_id}/variants", summary="An asset's stored renditions")
    async def list_variants(
        user: Annotated[CurrentUser, Depends(current_user)],
        asset_id: UUID,
        page: Annotated[PageParams, Depends(page_params)],
    ) -> dict[str, Any]:
        return await controller.list(user, asset_id, page)

    return router
