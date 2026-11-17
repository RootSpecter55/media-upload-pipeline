from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.variants.models import Variant


class VariantRepository(BaseRepository[Variant]):
    model = Variant

    async def for_asset(self, session: AsyncSession, asset_id: UUID) -> list[Variant]:
        rows = await session.scalars(select(Variant).where(Variant.asset_id == asset_id))
        return list(rows)

    async def by_name(self, session: AsyncSession, asset_id: UUID, name: str) -> Variant | None:
        return await session.scalar(
            select(Variant).where(Variant.asset_id == asset_id, Variant.name == name)
        )

    async def replace_all(self, session: AsyncSession, asset_id: UUID, rows: list[Variant]) -> None:
        """A retried run may have stored some rows already; start from a clean slate."""
        await session.execute(delete(Variant).where(Variant.asset_id == asset_id))
        session.add_all(rows)
        await session.flush()
