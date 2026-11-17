from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.core.auth import CurrentUser
from app.modules.uploads.controller import UploadsController
from app.modules.uploads.schemas import UploadRequest


def uploads_router(controller: UploadsController, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/uploads", tags=["uploads"])
    User = Annotated[CurrentUser, Depends(current_user)]

    @router.post("", status_code=201, summary="Reserve quota and get a presigned upload")
    async def request_upload(user: User, body: UploadRequest) -> dict[str, Any]:
        return await controller.request(user, body)

    @router.post(
        "/{asset_id}/complete",
        summary="Confirm the upload: 202 queued for processing, 200 for an existing asset",
    )
    async def complete(user: User, asset_id: UUID) -> Response:
        return await controller.complete(user, asset_id)

    return router
