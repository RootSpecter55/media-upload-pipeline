import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class JobRow(Base):
    """A queued job for the Postgres JobQueue. Claimed with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (Index("jobs_status_run_at", "status", "run_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(default=0)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # While running, the claim is valid until then; after it, another worker may take the job.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
