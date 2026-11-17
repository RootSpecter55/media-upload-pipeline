from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.users.models import User


class UserRepository(BaseRepository[User]):
    model = User

    async def by_key_hash(self, session: AsyncSession, key_hash: str) -> User | None:
        return await session.scalar(select(User).where(User.api_key_hash == key_hash))

    async def lock(self, session: AsyncSession, user_id: UUID) -> User | None:
        """The user row, locked until the transaction ends. Quota checks take this lock, so two
        requests from the same user are checked one after the other, never both at once."""
        return await session.scalar(select(User).where(User.id == user_id).with_for_update())
