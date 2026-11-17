from typing import Any

from app.core.pagination import data_envelope
from app.modules.users.schemas import CreateUserRequest
from app.modules.users.service import UsersService


class UsersController:
    def __init__(self, service: UsersService) -> None:
        self._service = service

    async def create(self, body: CreateUserRequest) -> dict[str, Any]:
        return data_envelope((await self._service.create(body)).to_json())
