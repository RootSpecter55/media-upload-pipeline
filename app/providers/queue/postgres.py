from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.providers.queue.base import Job
from app.providers.queue.models import JobRow


class PostgresJobQueue:
    """Jobs as rows. A worker claims one with SELECT ... FOR UPDATE SKIP LOCKED: a second worker
    skips the locked row instead of waiting for it or taking it too. The claim sets a lease
    (locked_until); if the worker dies, the job becomes claimable again when it runs out."""

    name = "postgres"

    def __init__(self, db: Database, lease_seconds: int = 300) -> None:
        self._db = db
        self._lease = timedelta(seconds=lease_seconds)

    async def enqueue(self, session: AsyncSession, kind: str, payload: dict[str, Any]) -> None:
        session.add(JobRow(kind=kind, payload=payload))
        await session.flush()

    async def claim(self) -> Job | None:
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            row = await session.scalar(
                select(JobRow)
                .where(
                    or_(
                        (JobRow.status == "queued") & (JobRow.run_at <= now),
                        (JobRow.status == "running") & (JobRow.locked_until < now),
                    )
                )
                .order_by(JobRow.run_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "running"
            row.attempts += 1
            row.locked_until = now + self._lease
            return Job(
                id=str(row.id),
                kind=row.kind,
                payload=row.payload,
                attempts=row.attempts,
                receipt=str(row.id),
            )

    async def complete(self, job: Job) -> None:
        async with self._db.transaction() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.id == job.receipt)
                .values(status="done", locked_until=None)
            )

    async def retry(self, job: Job, delay_seconds: float, error: str) -> None:
        async with self._db.transaction() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.id == job.receipt)
                .values(
                    status="queued",
                    locked_until=None,
                    last_error=error[:2000],
                    run_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                )
            )
