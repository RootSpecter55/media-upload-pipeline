from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.assets.models import ACTIVE_STATUSES, Asset, AssetStatus


class AssetRepository(BaseRepository[Asset]):
    model = Asset

    async def get_for_owner(
        self, session: AsyncSession, owner_id: UUID, asset_id: UUID, *, lock: bool = False
    ) -> Asset | None:
        query = select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id)
        return await session.scalar(query.with_for_update() if lock else query)

    async def lock(self, session: AsyncSession, asset_id: UUID) -> Asset | None:
        """The asset, row-locked, so two workers or requests cannot move it at the same time."""
        return await session.scalar(select(Asset).where(Asset.id == asset_id).with_for_update())

    async def find_duplicate(
        self, session: AsyncSession, owner_id: UUID, checksum: str, exclude_id: UUID
    ) -> Asset | None:
        """This user's other non-rejected asset with the same SHA-256, if any."""
        return await session.scalar(
            select(Asset).where(
                Asset.owner_id == owner_id,
                Asset.checksum_sha256 == checksum,
                Asset.status.in_(ACTIVE_STATUSES),
                Asset.id != exclude_id,
            )
        )

    async def expired_pending(
        self, session: AsyncSession, now: datetime, limit: int
    ) -> list[Asset]:
        """Uploads that were requested but never completed, whose upload URL has expired."""
        rows = await session.scalars(
            select(Asset)
            .where(Asset.status == AssetStatus.PENDING_UPLOAD, Asset.upload_expires_at < now)
            .order_by(Asset.upload_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows)
