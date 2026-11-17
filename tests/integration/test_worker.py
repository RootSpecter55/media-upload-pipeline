import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.container import Container, build_container
from app.core.config import get_settings
from app.core.db import Database, get_engine
from app.modules.assets.models import Asset
from app.modules.variants.service import RetryLaterError
from app.providers.queue.models import JobRow
from app.providers.queue.postgres import PostgresJobQueue
from app.providers.storage.base import StorageProvider
from tests.images import png
from tests.integration.conftest import create_user, drain, upload


class FlakyStorage:
    """Wraps the real storage; `get` fails for the first `failures` calls."""

    def __init__(self, inner: StorageProvider, failures: int) -> None:
        self._inner = inner
        self.failures = failures

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def get(self, key: str) -> bytes:
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("storage is having a bad day")
        return await self._inner.get(key)


async def _asset(asset_id: str) -> Asset:
    async with get_engine().connect() as connection:
        row = (await connection.execute(select(Asset).where(Asset.id == UUID(asset_id)))).one()
        return row  # type: ignore[return-value]


async def _uploaded_asset(client: AsyncClient) -> tuple[dict[str, str], str]:
    user = await create_user(client)
    _, completed = await upload(client, user, png(), "image/png")
    return user, completed["asset"]["id"]


class TestRetries:
    async def test_a_transient_failure_is_retried_and_then_succeeds(
        self, client: AsyncClient, container: Container
    ) -> None:
        flaky = FlakyStorage(container.processing._storage, failures=1)
        worker = build_container(get_settings(), storage=flaky)  # type: ignore[arg-type]
        user, asset_id = await _uploaded_asset(client)

        with pytest.raises(RetryLaterError) as retry:
            await worker.processing.process(UUID(asset_id))
        assert retry.value.delay_seconds == 10
        after_failure = await _asset(asset_id)
        assert (after_failure.status, after_failure.processing_attempts) == ("uploaded", 1)

        assert await worker.processing.process(UUID(asset_id)) == "ready"
        asset = (await client.get(f"/assets/{asset_id}", headers=user)).json()["data"]
        assert (asset["status"], asset["processingAttempts"]) == ("ready", 2)

    async def test_gives_up_after_the_maximum_attempts(
        self, client: AsyncClient, container: Container
    ) -> None:
        flaky = FlakyStorage(container.processing._storage, failures=99)
        worker = build_container(get_settings(), storage=flaky)  # type: ignore[arg-type]
        user, asset_id = await _uploaded_asset(client)

        delays = []
        for _ in range(2):
            with pytest.raises(RetryLaterError) as retry:
                await worker.processing.process(UUID(asset_id))
            delays.append(retry.value.delay_seconds)
        assert delays == [10, 20]  # exponential backoff
        assert await worker.processing.process(UUID(asset_id)) == "rejected"

        asset = (await client.get(f"/assets/{asset_id}", headers=user)).json()["data"]
        assert asset["status"] == "rejected"
        assert asset["processingAttempts"] == 3
        assert asset["rejectionReason"].startswith("processing_failed: ConnectionError")

    async def test_the_worker_reschedules_the_job_with_the_backoff(
        self, client: AsyncClient, container: Container
    ) -> None:
        flaky = FlakyStorage(container.processing._storage, failures=1)
        worker = build_container(get_settings(), storage=flaky)  # type: ignore[arg-type]
        await _uploaded_asset(client)

        await drain(worker)  # one attempt fails; the job is not due again yet
        async with get_engine().connect() as connection:
            job = (await connection.execute(select(JobRow))).one()
        assert job.status == "queued"
        assert job.last_error == "ConnectionError: storage is having a bad day"
        assert job.run_at > datetime.now(UTC) + timedelta(seconds=5)

    async def test_an_interrupted_run_is_taken_over(
        self, client: AsyncClient, container: Container
    ) -> None:
        _, asset_id = await _uploaded_asset(client)
        # A worker died after moving the asset to "processing".
        async with get_engine().begin() as connection:
            await connection.execute(
                update(Asset).where(Asset.id == UUID(asset_id)).values(status="processing")
            )
        assert await container.processing.process(UUID(asset_id)) == "ready"

    async def test_finished_assets_are_skipped_when_a_job_is_delivered_twice(
        self, client: AsyncClient, container: Container
    ) -> None:
        _, asset_id = await _uploaded_asset(client)
        assert await container.processing.process(UUID(asset_id)) == "ready"
        assert await container.processing.process(UUID(asset_id)) == "skipped"


class TestPostgresQueue:
    async def test_two_workers_never_claim_the_same_job(self) -> None:
        db = Database(get_engine())
        first, second = PostgresJobQueue(db), PostgresJobQueue(db)
        async with db.transaction() as session:
            for _ in range(30):
                await first.enqueue(session, "process_asset", {"assetId": str(uuid4())})

        async def take_all(queue: PostgresJobQueue) -> list[str]:
            claimed = []
            while (job := await queue.claim()) is not None:
                claimed.append(job.id)
            return claimed

        a, b = await asyncio.gather(take_all(first), take_all(second))
        assert len(a) + len(b) == 30
        assert set(a).isdisjoint(b)
        assert a and b  # both workers got work

    async def test_an_expired_lease_makes_the_job_claimable_again(self) -> None:
        db = Database(get_engine())
        queue = PostgresJobQueue(db, lease_seconds=60)
        async with db.transaction() as session:
            await queue.enqueue(session, "process_asset", {"assetId": str(uuid4())})
        job = await queue.claim()
        assert job is not None
        assert await queue.claim() is None  # leased

        async with get_engine().begin() as connection:
            await connection.execute(
                update(JobRow).values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        again = await queue.claim()
        assert again is not None
        assert (again.id, again.attempts) == (job.id, 2)
