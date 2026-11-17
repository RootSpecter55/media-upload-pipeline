from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.schemas import ApiModel
from app.modules.assets.schemas import AssetOut

ImageContentType = Literal["image/jpeg", "image/png", "image/webp"]


class UploadRequest(ApiModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: ImageContentType
    # The exact or maximum size of the file; storage refuses anything larger.
    size_bytes: int = Field(gt=0)


class UploadInstructions(ApiModel):
    """POST multipart/form-data to `url`: every field in `fields`, then the file as "file"."""

    url: str
    method: Literal["POST"] = "POST"
    fields: dict[str, str]
    expires_at: datetime


class UploadTicket(ApiModel):
    asset: AssetOut
    upload: UploadInstructions


class CompletedUpload(ApiModel):
    asset: AssetOut
    # True when the same user already had this exact file: `asset` is that existing one.
    duplicate: bool
