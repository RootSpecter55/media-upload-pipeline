from datetime import datetime
from typing import Literal
from uuid import UUID

from app.core.schemas import ApiModel

VariantName = Literal["original", "large", "thumbnail"]


class AssetOut(ApiModel):
    id: UUID
    status: str
    original_filename: str
    declared_content_type: str
    declared_size_bytes: int
    size_bytes: int | None
    checksum_sha256: str | None
    format: str | None
    width: int | None
    height: int | None
    rejection_reason: str | None
    processing_attempts: int
    created_at: datetime
    ready_at: datetime | None


class DownloadOut(ApiModel):
    url: str
    expires_at: datetime
