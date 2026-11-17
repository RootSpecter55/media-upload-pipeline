import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.container import Container, build_container
from app.core.config import get_settings
from app.core.db import get_engine
from app.main import create_app
from app.worker import run_one

ADMIN = {"Authorization": f"Bearer {os.environ['ADMIN_API_KEY']}"}


async def _create_database_if_missing() -> None:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    base, db_name = url.rsplit("/", 1)
    connection = await asyncpg.connect(f"{base}/postgres")
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await connection.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    """Creates the test database if needed and applies migrations once per session."""
    await _create_database_if_missing()
    await asyncio.to_thread(
        subprocess.run,
        ["alembic", "upgrade", "head"],
        check=True,
        capture_output=True,
        env=os.environ,
    )
    yield
    await get_engine().dispose()


@pytest.fixture(autouse=True)
async def clean_tables() -> None:
    """Every test starts empty. Deleting users cascades to assets and variants."""
    async with get_engine().begin() as connection:
        await connection.execute(text("TRUNCATE users, jobs CASCADE"))


@pytest.fixture(scope="session")
def container() -> Container:
    return build_container(get_settings())


@pytest.fixture(scope="session")
def app(container: Container) -> Any:
    return create_app(container)


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def create_user(client: AsyncClient, quota_bytes: int | None = None) -> dict[str, str]:
    """Creates a user; returns the auth header for its API key."""
    body: dict[str, Any] = {"name": "Test user"}
    if quota_bytes is not None:
        body["quotaBytes"] = quota_bytes
    response = await client.post("/users", headers=ADMIN, json=body)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['apiKey']}"}


async def request_upload(
    client: AsyncClient,
    user: dict[str, str],
    size: int,
    content_type: str = "image/jpeg",
    filename: str = "photo.jpg",
) -> dict[str, Any]:
    response = await client.post(
        "/uploads",
        headers=user,
        json={"filename": filename, "contentType": content_type, "sizeBytes": size},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def post_to_storage(client: AsyncClient, ticket: dict[str, Any], data: bytes) -> int:
    """What a browser does with the presigned POST: every field, then the file last."""
    upload = ticket["upload"]
    response = await client.post(
        upload["url"],
        data=upload["fields"],
        files={"file": ("upload", data, upload["fields"]["Content-Type"])},
    )
    return response.status_code


async def upload(
    client: AsyncClient,
    user: dict[str, str],
    data: bytes,
    content_type: str = "image/jpeg",
) -> tuple[int, dict[str, Any]]:
    """Request, upload to storage, complete. Returns (complete status, response data)."""
    ticket = await request_upload(client, user, len(data), content_type)
    assert await post_to_storage(client, ticket, data) == 204
    response = await client.post(f"/uploads/{ticket['asset']['id']}/complete", headers=user)
    return response.status_code, response.json()["data"]


async def storage_keys(asset_id: str) -> list[str]:
    """Every storage key recorded for an asset: the raw upload and its variants."""
    async with get_engine().connect() as connection:
        upload_key = await connection.scalar(
            text("SELECT upload_key FROM assets WHERE id = :id"), {"id": asset_id}
        )
        variant_keys = await connection.scalars(
            text("SELECT storage_key FROM variants WHERE asset_id = :id"), {"id": asset_id}
        )
        return [upload_key, *variant_keys]


async def drain(container: Container) -> None:
    """Runs the worker until no job is due."""
    while await run_one(container):
        pass
