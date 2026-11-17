from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel


class QuotaOut(ApiModel):
    user_id: UUID
    quota_bytes: int
    used_bytes: int
    available_bytes: int


class SetQuotaRequest(ApiModel):
    quota_bytes: int = Field(ge=0)
