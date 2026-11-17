from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.models import ACTIVE_STATUSES, Asset


class QuotaRepository:
    async def used_bytes(self, session: AsyncSession, owner_id: UUID) -> int:
        """Bytes a user is using or has reserved: the real size once known, otherwise the size
        declared when the upload URL was issued (so a pending upload holds its space)."""
        total = await session.scalar(
            select(
                func.coalesce(
                    func.sum(func.coalesce(Asset.size_bytes, Asset.declared_size_bytes)), 0
                )
            ).where(Asset.owner_id == owner_id, Asset.status.in_(ACTIVE_STATUSES))
        )
        return int(total or 0)
