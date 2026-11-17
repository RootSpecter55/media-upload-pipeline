from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.db import Database
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams
from app.modules.assets.models import Asset, AssetStatus
from app.modules.assets.repository import AssetRepository
from app.modules.assets.schemas import AssetOut, DownloadOut
from app.modules.variants.repository import VariantRepository
from app.providers.storage.base import StorageProvider


class AssetsService:
    def __init__(
        self,
        db: Database,
        assets: AssetRepository,
        variants: VariantRepository,
        storage: StorageProvider,
        download_ttl_seconds: int,
    ) -> None:
        self._db = db
        self._assets = assets
        self._variants = variants
        self._storage = storage
        self._download_ttl = download_ttl_seconds

    async def list(
        self, owner_id: UUID, status: AssetStatus | None, page: PageParams
    ) -> tuple[list[AssetOut], int]:
        filters = [Asset.owner_id == owner_id]
        if status:
            filters.append(Asset.status == status)
        async with self._db.transaction() as session:
            rows, total = await self._assets.list(
                session,
                *filters,
                order_by=Asset.created_at.desc(),
                offset=page.offset,
                limit=page.page_size,
            )
        return [AssetOut.model_validate(a) for a in rows], total

    async def get(self, owner_id: UUID, asset_id: UUID) -> AssetOut:
        async with self._db.transaction() as session:
            asset = await self._assets.get_for_owner(session, owner_id, asset_id)
        if asset is None:
            raise NotFoundError("Asset")
        return AssetOut.model_validate(asset)

    async def download(self, owner_id: UUID, asset_id: UUID, variant_name: str) -> DownloadOut:
        """A short-lived signed URL. Only `ready` assets have variants; the raw upload (which may
        still contain GPS metadata) is never offered for download."""
        async with self._db.transaction() as session:
            asset = await self._assets.get_for_owner(session, owner_id, asset_id)
            if asset is None:
                raise NotFoundError("Asset")
            if asset.status != AssetStatus.READY:
                raise ConflictError(f"Asset is not ready (it is {asset.status})")
            variant = await self._variants.by_name(session, asset_id, variant_name)
        if variant is None:
            raise NotFoundError(f"Variant {variant_name}")
        return DownloadOut(
            url=self._storage.presign_download(variant.storage_key, self._download_ttl),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._download_ttl),
        )

    async def delete(self, owner_id: UUID, asset_id: UUID) -> None:
        """Deletes the row (variants cascade), then every stored object. A processing asset is
        refused, so the worker never writes variants for a row that is gone."""
        async with self._db.transaction() as session:
            asset = await self._assets.get_for_owner(session, owner_id, asset_id, lock=True)
            if asset is None:
                raise NotFoundError("Asset")
            if asset.status == AssetStatus.PROCESSING:
                raise ConflictError("Asset is being processed; try again shortly")
            keys = [asset.upload_key] + [
                v.storage_key for v in await self._variants.for_asset(session, asset_id)
            ]
            await self._assets.delete(session, asset)
        for key in keys:
            await self._storage.delete(key)
