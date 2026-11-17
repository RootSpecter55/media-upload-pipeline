from typing import Any
from uuid import UUID

from app.core.auth import CurrentUser
from app.core.pagination import data_envelope
from app.modules.quotas.schemas import SetQuotaRequest
from app.modules.quotas.service import QuotasService


class QuotasController:
    def __init__(self, service: QuotasService) -> None:
        self._service = service

    async def me(self, user: CurrentUser) -> dict[str, Any]:
        return data_envelope((await self._service.get(user.id)).to_json())

    async def set_limit(self, user_id: UUID, body: SetQuotaRequest) -> dict[str, Any]:
        return data_envelope((await self._service.set_limit(user_id, body.quota_bytes)).to_json())
