from typing import Any
from uuid import UUID

from app.core.auth import CurrentUser
from app.core.pagination import PageParams, data_envelope, page_envelope
from app.modules.assets.models import AssetStatus
from app.modules.assets.service import AssetsService


class AssetsController:
    def __init__(self, service: AssetsService) -> None:
        self._service = service

    async def list(
        self, user: CurrentUser, status: AssetStatus | None, page: PageParams
    ) -> dict[str, Any]:
        items, total = await self._service.list(user.id, status, page)
        return page_envelope([a.to_json() for a in items], total, page)

    async def get(self, user: CurrentUser, asset_id: UUID) -> dict[str, Any]:
        return data_envelope((await self._service.get(user.id, asset_id)).to_json())

    async def download(self, user: CurrentUser, asset_id: UUID, variant: str) -> dict[str, Any]:
        return data_envelope((await self._service.download(user.id, asset_id, variant)).to_json())

    async def delete(self, user: CurrentUser, asset_id: UUID) -> None:
        await self._service.delete(user.id, asset_id)
