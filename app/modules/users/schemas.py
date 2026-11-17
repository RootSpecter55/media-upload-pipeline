from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel


class CreateUserRequest(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    # Defaults to DEFAULT_QUOTA_BYTES.
    quota_bytes: int | None = Field(default=None, ge=0)


class UserOut(ApiModel):
    id: UUID
    name: str
    quota_bytes: int
    created_at: datetime


class CreatedUser(ApiModel):
    user: UserOut
    api_key: str
