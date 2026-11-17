from datetime import datetime
from uuid import UUID

from app.core.schemas import ApiModel


class VariantOut(ApiModel):
    id: UUID
    asset_id: UUID
    name: str
    content_type: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime
