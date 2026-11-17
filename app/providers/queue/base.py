from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    # Broker handle for acknowledging (the row id for Postgres, the receipt handle for SQS).
    receipt: str


class JobQueue(Protocol):
    """Background jobs. Implementations: Postgres table (default) and SQS.

    Delivery is at-least-once: a job whose worker dies is handed out again, so handlers must be
    safe to run twice (the asset state machine makes them so).
    """

    name: str

    async def enqueue(self, session: AsyncSession, kind: str, payload: dict[str, Any]) -> None:
        """Adds a job. The Postgres queue uses `session`, so the job commits (or rolls back)
        with the caller's transaction."""
        ...

    async def claim(self) -> Job | None:
        """The next due job, or None. It stays hidden from other workers while claimed."""
        ...

    async def complete(self, job: Job) -> None: ...

    async def retry(self, job: Job, delay_seconds: float, error: str) -> None: ...
