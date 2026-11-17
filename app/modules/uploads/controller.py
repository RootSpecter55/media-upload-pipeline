from typing import Any
from uuid import UUID

from fastapi import Response
from fastapi.responses import JSONResponse

from app.core.auth import CurrentUser
from app.core.pagination import data_envelope
from app.modules.uploads.schemas import UploadRequest
from app.modules.uploads.service import UploadsService


class UploadsController:
    def __init__(self, service: UploadsService) -> None:
        self._service = service

    async def request(self, user: CurrentUser, body: UploadRequest) -> dict[str, Any]:
        return data_envelope((await self._service.request(user, body)).to_json())

    async def complete(self, user: CurrentUser, asset_id: UUID) -> Response:
        """202 when processing was queued; 200 when the answer is an existing asset."""
        result = await self._service.complete(user, asset_id)
        queued = not result.duplicate and result.asset.status == "uploaded"
        return JSONResponse(data_envelope(result.to_json()), status_code=202 if queued else 200)
