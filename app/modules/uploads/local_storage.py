"""The endpoints that stand in for S3 when STORAGE_PROVIDER=local. They are authorised by the
signature in the URL or form fields, not by an API key, exactly like S3 presigned requests.
Mounted only for the local provider."""

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile

from app.core.errors import AppError, NotFoundError
from app.providers.storage.local import LocalDiskStorage


class LocalStorageController:
    def __init__(self, storage: LocalDiskStorage) -> None:
        self._storage = storage

    async def upload(self, fields: dict[str, str], file: UploadFile) -> Response:
        key, content_type, max_bytes = self._storage.check_upload(fields)
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            # S3 answers EntityTooLarge when the content-length-range condition fails.
            raise AppError(413, "ENTITY_TOO_LARGE", "File is larger than the signed limit")
        if not data:
            raise AppError(400, "EMPTY_FILE", "The file is empty")
        await self._storage.put(key, data, content_type)
        return Response(status_code=204)

    async def download(self, key: str, expires: int, signature: str) -> Response:
        self._storage.check_download(key, expires, signature)
        info = await self._storage.head(key)
        if info is None:
            raise NotFoundError("File")
        return Response(await self._storage.get(key), media_type=info.content_type)


def local_storage_router(controller: LocalStorageController) -> APIRouter:
    router = APIRouter(prefix="/storage", tags=["local storage"])

    @router.post("/upload", status_code=204, summary="Signed form upload (local S3 stand-in)")
    async def upload(
        key: Annotated[str, Form()],
        content_type: Annotated[str, Form(alias="Content-Type")],
        max_bytes: Annotated[str, Form(alias="maxBytes")],
        expires: Annotated[str, Form()],
        signature: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> Response:
        fields = {
            "key": key,
            "Content-Type": content_type,
            "maxBytes": max_bytes,
            "expires": expires,
            "signature": signature,
        }
        return await controller.upload(fields, file)

    @router.get("/files/{key:path}", summary="Signed download (local S3 stand-in)")
    async def download(key: str, expires: int, signature: str) -> Response:
        return await controller.download(key, expires, signature)

    return router
