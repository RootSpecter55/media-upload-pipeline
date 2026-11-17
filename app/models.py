"""Imports every ORM model so Base.metadata is complete (Alembic and tests use this)."""

from app.core.db import Base
from app.modules.assets.models import Asset
from app.modules.users.models import User
from app.modules.variants.models import Variant
from app.providers.queue.models import JobRow

__all__ = ["Asset", "Base", "JobRow", "User", "Variant"]
