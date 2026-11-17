import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError
from app.core.pagination import PageParams
from app.modules.assets.models import AssetStatus
from app.modules.assets.repository import AssetRepository
from app.modules.assets.state import transition
from app.modules.variants.models import Variant
from app.modules.variants.pipeline import EXTENSIONS, ImageRejectedError, process_image
from app.modules.variants.repository import VariantRepository
from app.modules.variants.schemas import VariantOut
from app.providers.storage.base import StorageProvider

logger = logging.getLogger(__name__)

Outcome = Literal["ready", "rejected", "skipped"]


class RetryLaterError(Exception):
    """Processing failed for a reason that may go away (storage hiccup, crash). Try again."""

    def __init__(self, delay_seconds: float, message: str) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds


@dataclass(frozen=True)
class ProcessingOptions:
    max_pixels: int
    sizes: dict[str, int]
    max_attempts: int
    retry_base_seconds: float = 10.0


class ProcessingService:
    """What the worker does for one asset. Safe to run twice for the same asset: the state
    machine decides whether there is anything left to do."""

    def __init__(
        self,
        db: Database,
        assets: AssetRepository,
        variants: VariantRepository,
        storage: StorageProvider,
        options: ProcessingOptions,
    ) -> None:
        self._db = db
        self._assets = assets
        self._variants = variants
        self._storage = storage
        self._options = options

    async def process(self, asset_id: UUID) -> Outcome:
        async with self._db.transaction() as session:
            asset = await self._assets.lock(session, asset_id)
            if asset is None or asset.status not in (AssetStatus.UPLOADED, AssetStatus.PROCESSING):
                return "skipped"  # deleted, already finished, or never completed
            if asset.status == AssetStatus.PROCESSING:
                # A previous run died mid-way (its job lease ran out): take the retry path.
                transition(asset, AssetStatus.UPLOADED)
            transition(asset, AssetStatus.PROCESSING)
            asset.processing_attempts += 1
            owner_id, upload_key = asset.owner_id, asset.upload_key
            declared, attempts = asset.declared_content_type, asset.processing_attempts

        try:
            data = await self._storage.get(upload_key)
            result = await asyncio.to_thread(
                process_image, data, declared, self._options.max_pixels, self._options.sizes
            )
            rows = []
            for rendered in result.variants:
                key = f"variants/{owner_id}/{asset_id}/{rendered.name}.{EXTENSIONS[result.format]}"
                await self._storage.put(key, rendered.data, rendered.content_type)
                rows.append(
                    Variant(
                        asset_id=asset_id,
                        name=rendered.name,
                        storage_key=key,
                        content_type=rendered.content_type,
                        width=rendered.width,
                        height=rendered.height,
                        size_bytes=len(rendered.data),
                    )
                )
        except ImageRejectedError as exc:
            await self._finish_rejected(asset_id, exc.reason, upload_key)
            return "rejected"
        except Exception as exc:
            return await self._failed(asset_id, attempts, upload_key, exc)

        async with self._db.transaction() as session:
            asset = await self._assets.lock(session, asset_id)
            if asset is None:
                return "skipped"
            await self._variants.replace_all(session, asset_id, rows)
            asset.format, asset.width, asset.height = result.format, result.width, result.height
            asset.ready_at = datetime.now(UTC)
            transition(asset, AssetStatus.READY)
        # The raw upload may still contain GPS data; only the sanitised variants are kept.
        await self._storage.delete(upload_key)
        return "ready"

    async def _finish_rejected(self, asset_id: UUID, reason: str, upload_key: str) -> None:
        async with self._db.transaction() as session:
            asset = await self._assets.lock(session, asset_id)
            if asset is not None:
                asset.rejection_reason = reason[:300]
                transition(asset, AssetStatus.REJECTED)
        await self._storage.delete(upload_key)
        logger.info("Asset %s rejected: %s", asset_id, reason)

    async def _failed(
        self, asset_id: UUID, attempts: int, upload_key: str, exc: Exception
    ) -> Outcome:
        """An unexpected error: back to `uploaded` for another try, or rejected at the limit."""
        message = f"{type(exc).__name__}: {exc}"
        if attempts >= self._options.max_attempts:
            await self._finish_rejected(asset_id, f"processing_failed: {message}", upload_key)
            return "rejected"
        async with self._db.transaction() as session:
            asset = await self._assets.lock(session, asset_id)
            if asset is not None:
                transition(asset, AssetStatus.UPLOADED)
        delay = self._options.retry_base_seconds * 2 ** (attempts - 1)
        logger.warning("Asset %s attempt %d failed (%s); retrying", asset_id, attempts, message)
        raise RetryLaterError(delay, message) from exc


class VariantsService:
    def __init__(self, db: Database, assets: AssetRepository, variants: VariantRepository) -> None:
        self._db = db
        self._assets = assets
        self._variants = variants

    async def list(
        self, owner_id: UUID, asset_id: UUID, page: PageParams
    ) -> tuple[list[VariantOut], int]:
        async with self._db.transaction() as session:
            if await self._assets.get_for_owner(session, owner_id, asset_id) is None:
                raise NotFoundError("Asset")
            rows, total = await self._variants.list(
                session,
                Variant.asset_id == asset_id,
                order_by=Variant.width.desc(),
                offset=page.offset,
                limit=page.page_size,
            )
        return [VariantOut.model_validate(v) for v in rows], total
