from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.quotas.repository import QuotaRepository
from app.modules.quotas.schemas import QuotaOut
from app.modules.users.repository import UserRepository


class QuotasService:
    def __init__(self, db: Database, users: UserRepository, quotas: QuotaRepository) -> None:
        self._db = db
        self._users = users
        self._quotas = quotas

    async def require_space(
        self, session: AsyncSession, user_id: UUID, extra_bytes: int, *, already_counted: int = 0
    ) -> None:
        """Raises 403 QUOTA_EXCEEDED unless `extra_bytes` more fit. Locks the user row for the
        rest of the caller's transaction, so parallel requests are checked one at a time.

        `already_counted` is space the usage already includes for this request (a pending
        upload's reservation), so it is not counted twice.
        """
        user = await self._users.lock(session, user_id)
        if user is None:
            raise NotFoundError("User")
        used = await self._quotas.used_bytes(session, user_id) - already_counted
        if used + extra_bytes > user.quota_bytes:
            raise ForbiddenError(
                "QUOTA_EXCEEDED",
                "This upload would exceed your storage quota",
                {"quotaBytes": user.quota_bytes, "usedBytes": used, "requestedBytes": extra_bytes},
            )

    async def get(self, user_id: UUID) -> QuotaOut:
        async with self._db.transaction() as session:
            user = await self._users.get(session, user_id)
            if user is None:
                raise NotFoundError("User")
            used = await self._quotas.used_bytes(session, user_id)
        return QuotaOut(
            user_id=user_id,
            quota_bytes=user.quota_bytes,
            used_bytes=used,
            available_bytes=max(0, user.quota_bytes - used),
        )

    async def set_limit(self, user_id: UUID, quota_bytes: int) -> QuotaOut:
        async with self._db.transaction() as session:
            user = await self._users.lock(session, user_id)
            if user is None:
                raise NotFoundError("User")
            user.quota_bytes = quota_bytes
        return await self.get(user_id)
