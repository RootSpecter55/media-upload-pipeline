from app.core.auth import CurrentUser
from app.core.db import Database
from app.core.security import new_api_key, sha256_hex
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import CreatedUser, CreateUserRequest, UserOut


class UsersService:
    def __init__(self, db: Database, users: UserRepository, default_quota_bytes: int) -> None:
        self._db = db
        self._users = users
        self._default_quota = default_quota_bytes

    async def create(self, request: CreateUserRequest) -> CreatedUser:
        """Returns the API key once; only its hash is stored."""
        api_key = new_api_key()
        quota = self._default_quota if request.quota_bytes is None else request.quota_bytes
        async with self._db.transaction() as session:
            user = await self._users.add(
                session,
                User(name=request.name, api_key_hash=sha256_hex(api_key), quota_bytes=quota),
            )
        return CreatedUser(user=UserOut.model_validate(user), api_key=api_key)

    async def resolve(self, api_key: str) -> CurrentUser | None:
        """Used by the auth dependency."""
        async with self._db.transaction() as session:
            user = await self._users.by_key_hash(session, sha256_hex(api_key))
        return CurrentUser(id=user.id, name=user.name) if user else None
