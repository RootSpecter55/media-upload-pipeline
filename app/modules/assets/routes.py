from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.auth import CurrentUser
from app.core.pagination import PageParams, page_params
from app.modules.assets.controller import AssetsController
from app.modules.assets.models import AssetStatus
from app.modules.assets.schemas import VariantName


def assets_router(controller: AssetsController, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/assets", tags=["assets"])
    User = Annotated[CurrentUser, Depends(current_user)]

    @router.get("", summary="My assets, newest first")
    async def list_assets(
        user: User,
        page: Annotated[PageParams, Depends(page_params)],
        status: AssetStatus | None = None,
    ) -> dict[str, Any]:
        return await controller.list(user, status, page)

    @router.get("/{asset_id}", summary="One asset")
    async def get(user: User, asset_id: UUID) -> dict[str, Any]:
        return await controller.get(user, asset_id)

    @router.get("/{asset_id}/download", summary="A short-lived signed URL for one variant")
    async def download(
        user: User, asset_id: UUID, variant: Annotated[VariantName, Query()] = "original"
    ) -> dict[str, Any]:
        return await controller.download(user, asset_id, variant)

    @router.delete("/{asset_id}", status_code=204, summary="Delete an asset and its files")
    async def delete(user: User, asset_id: UUID) -> Response:
        await controller.delete(user, asset_id)
        return Response(status_code=204)

    return router
