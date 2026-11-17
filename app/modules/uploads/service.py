import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.core.auth import CurrentUser
from app.core.db import Database
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.modules.assets.models import Asset, AssetStatus
from app.modules.assets.repository import AssetRepository
from app.modules.assets.schemas import AssetOut
from app.modules.assets.state import transition
from app.modules.quotas.service import QuotasService
from app.modules.uploads.schemas import (
    CompletedUpload,
    UploadInstructions,
    UploadRequest,
    UploadTicket,
)
from app.providers.queue.base import JobQueue
from app.providers.storage.base import StorageProvider

PROCESS_ASSET = "process_asset"


class UploadsService:
    def __init__(
        self,
        db: Database,
        assets: AssetRepository,
        quotas: QuotasService,
        storage: StorageProvider,
        queue: JobQueue,
        *,
        max_upload_bytes: int,
        upload_ttl_seconds: int,
    ) -> None:
        self._db = db
        self._assets = assets
        self._quotas = quotas
        self._storage = storage
        self._queue = queue
        self._max_bytes = max_upload_bytes
        self._ttl = upload_ttl_seconds

    async def request(self, user: CurrentUser, body: UploadRequest) -> UploadTicket:
        """Reserves the declared size against the quota and returns a presigned upload. The file
        goes straight to storage; this API never sees the bytes."""
        if body.size_bytes > self._max_bytes:
            raise AppError(413, "FILE_TOO_LARGE", f"Files may be at most {self._max_bytes} bytes")
        asset_id = uuid.uuid4()
        async with self._db.transaction() as session:
            await self._quotas.require_space(session, user.id, body.size_bytes)
            asset = await self._assets.add(
                session,
                Asset(
                    id=asset_id,
                    owner_id=user.id,
                    original_filename=body.filename,
                    declared_content_type=body.content_type,
                    declared_size_bytes=body.size_bytes,
                    upload_key=f"uploads/{user.id}/{asset_id}",
                    upload_expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl),
                ),
            )
        presigned = self._storage.presign_upload(
            asset.upload_key, body.content_type, body.size_bytes, self._ttl
        )
        return UploadTicket(
            asset=AssetOut.model_validate(asset),
            upload=UploadInstructions(
                url=presigned.url, fields=presigned.fields, expires_at=presigned.expires_at
            ),
        )

    async def complete(self, user: CurrentUser, asset_id: uuid.UUID) -> CompletedUpload:
        """Called after the client's upload. Checks the object, deduplicates by SHA-256, checks
        the quota again with the real size, then marks the asset uploaded and enqueues
        processing, in one transaction (with the Postgres queue, the job commits with it)."""
        async with self._db.transaction() as session:
            asset = await self._assets.get_for_owner(session, user.id, asset_id)
        if asset is None:
            raise NotFoundError("Asset")
        if asset.status != AssetStatus.PENDING_UPLOAD:
            # Completing twice (e.g. a retried request) just returns the asset.
            return CompletedUpload(asset=AssetOut.model_validate(asset), duplicate=False)

        info = await self._storage.head(asset.upload_key)
        if info is None:
            raise ConflictError("No file has been uploaded for this asset yet")
        if info.size_bytes > asset.declared_size_bytes:
            raise AppError(413, "FILE_TOO_LARGE", "The uploaded file is larger than declared")
        data = await self._storage.get(asset.upload_key)
        checksum = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())

        try:
            return await self._finish(user, asset_id, info.size_bytes, checksum)
        except IntegrityError:
            # Another upload of the same file completed at the same moment and won the unique
            # index; this one becomes a duplicate of it.
            return await self._finish(user, asset_id, info.size_bytes, checksum)
        except ForbiddenError:
            await self._discard(user, asset_id)
            raise

    async def _finish(
        self, user: CurrentUser, asset_id: uuid.UUID, size: int, checksum: str
    ) -> CompletedUpload:
        async with self._db.transaction() as session:
            asset = await self._assets.get_for_owner(session, user.id, asset_id, lock=True)
            if asset is None:
                raise NotFoundError("Asset")
            if asset.status != AssetStatus.PENDING_UPLOAD:
                return CompletedUpload(asset=AssetOut.model_validate(asset), duplicate=False)

            existing = await self._assets.find_duplicate(session, user.id, checksum, asset_id)
            if existing is not None:
                upload_key = asset.upload_key
                await self._assets.delete(session, asset)
                result = CompletedUpload(asset=AssetOut.model_validate(existing), duplicate=True)
            else:
                await self._quotas.require_space(
                    session, user.id, size, already_counted=asset.declared_size_bytes
                )
                asset.size_bytes, asset.checksum_sha256 = size, checksum
                transition(asset, AssetStatus.UPLOADED)
                await session.flush()  # the unique (owner, checksum) index is checked here
                await self._queue.enqueue(session, PROCESS_ASSET, {"assetId": str(asset.id)})
                return CompletedUpload(asset=AssetOut.model_validate(asset), duplicate=False)
        await self._storage.delete(upload_key)
        return result

    async def _discard(self, user: CurrentUser, asset_id: uuid.UUID) -> None:
        """Removes an upload that cannot be kept (over quota): the row and the stored file."""
        async with self._db.transaction() as session:
            asset = await self._assets.get_for_owner(session, user.id, asset_id, lock=True)
            if asset is None or asset.status != AssetStatus.PENDING_UPLOAD:
                return
            upload_key = asset.upload_key
            await self._assets.delete(session, asset)
        await self._storage.delete(upload_key)

    async def cleanup_expired(self, now: datetime | None = None, batch_size: int = 100) -> int:
        """Deletes uploads that were requested but never completed before their URL expired,
        and any file already written for them. Returns how many were removed."""
        now = now or datetime.now(UTC)
        removed = 0
        while True:
            async with self._db.transaction() as session:
                expired = await self._assets.expired_pending(session, now, batch_size)
                keys = [asset.upload_key for asset in expired]
                for asset in expired:
                    await self._assets.delete(session, asset)
            for key in keys:
                await self._storage.delete(key)
            removed += len(keys)
            if len(keys) < batch_size:
                return removed
