import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AssetStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    REJECTED = "rejected"


# Statuses that hold storage: they count towards the quota and block duplicate uploads.
ACTIVE_STATUSES = (
    AssetStatus.PENDING_UPLOAD,
    AssetStatus.UPLOADED,
    AssetStatus.PROCESSING,
    AssetStatus.READY,
)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        # One copy of each file per user. Enforced by the database, so two uploads of the same
        # file completing at the same moment cannot both win.
        Index(
            "assets_owner_checksum_unique",
            "owner_id",
            "checksum_sha256",
            unique=True,
            postgresql_where=text("status IN ('uploaded', 'processing', 'ready')"),
        ),
        Index("assets_owner_created", "owner_id", "created_at"),
        Index("assets_status_upload_expires", "status", "upload_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default=AssetStatus.PENDING_UPLOAD)

    # What the client said, when it asked for the upload URL. Never trusted for processing.
    original_filename: Mapped[str] = mapped_column(String(255))
    declared_content_type: Mapped[str] = mapped_column(String(50))
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger)
    upload_key: Mapped[str] = mapped_column(String(300))
    upload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # What the service found.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    format: Mapped[str | None] = mapped_column(String(10))
    width: Mapped[int | None]
    height: Mapped[int | None]
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    processing_attempts: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
