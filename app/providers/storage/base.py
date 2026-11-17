from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class PresignedUpload:
    """Where and how the client uploads: POST multipart/form-data to `url` with every field in
    `fields`, then the file as the last part, named "file". Same shape for S3 and local."""

    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int
    content_type: str


class StorageProvider(Protocol):
    """Where originals and variants live. Implementations: local disk (default) and S3."""

    name: str

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload: ...

    def presign_download(self, key: str, expires_in: int) -> str: ...

    async def head(self, key: str) -> ObjectInfo | None: ...

    async def get(self, key: str) -> bytes: ...

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def delete(self, key: str) -> None: ...
