from typing import Any
from uuid import UUID

from app.core.auth import CurrentUser
from app.core.pagination import PageParams, page_envelope
from app.modules.variants.service import VariantsService


class VariantsController:
    def __init__(self, service: VariantsService) -> None:
        self._service = service

    async def list(self, user: CurrentUser, asset_id: UUID, page: PageParams) -> dict[str, Any]:
        items, total = await self._service.list(user.id, asset_id, page)
        return page_envelope([v.to_json() for v in items], total, page)
