import os
import time
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.container import Container
from app.core.db import get_engine
from app.core.security import sign
from app.modules.assets.models import Asset
from tests.images import png
from tests.integration.conftest import (
    ADMIN,
    create_user,
    post_to_storage,
    request_upload,
    storage_keys,
    upload,
)


async def _assets() -> int:
    async with get_engine().connect() as connection:
        return int(await connection.scalar(select(func.count()).select_from(Asset)) or 0)


class TestQuota:
    async def test_checked_when_the_url_is_issued_counting_pending_reservations(
        self, client: AsyncClient
    ) -> None:
        user = await create_user(client, quota_bytes=3000)
        too_big = await client.post(
            "/uploads",
            headers=user,
            json={"filename": "a.png", "contentType": "image/png", "sizeBytes": 3001},
        )
        assert too_big.status_code == 403
        assert too_big.json()["error"]["code"] == "QUOTA_EXCEEDED"

        await request_upload(client, user, 2000, "image/png")  # reserves 2000
        second = await client.post(
            "/uploads",
            headers=user,
            json={"filename": "b.png", "contentType": "image/png", "sizeBytes": 2000},
        )
        assert second.status_code == 403
        assert second.json()["error"]["details"] == {
            "quotaBytes": 3000,
            "usedBytes": 2000,
            "requestedBytes": 2000,
        }
        me = (await client.get("/quotas/me", headers=user)).json()["data"]
        assert (me["usedBytes"], me["availableBytes"]) == (2000, 1000)

    async def test_checked_again_on_completion(self, client: AsyncClient) -> None:
        user = await create_user(client)
        data = png()
        ticket = await request_upload(client, user, len(data), "image/png")
        assert await post_to_storage(client, ticket, data) == 204

        # The quota shrinks between issuing the URL and completing.
        user_id = (await client.get("/quotas/me", headers=user)).json()["data"]["userId"]
        await client.put(f"/quotas/{user_id}", headers=ADMIN, json={"quotaBytes": 10})

        response = await client.post(f"/uploads/{ticket['asset']['id']}/complete", headers=user)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "QUOTA_EXCEEDED"
        assert await _assets() == 0  # the upload was discarded

    async def test_setting_quotas_needs_the_admin_key(self, client: AsyncClient) -> None:
        user = await create_user(client)
        me = (await client.get("/quotas/me", headers=user)).json()["data"]
        response = await client.put(f"/quotas/{me['userId']}", headers=user, json={"quotaBytes": 1})
        assert response.status_code == 401


class TestSignedUpload:
    async def test_rejects_a_tampered_signature(self, client: AsyncClient) -> None:
        user = await create_user(client)
        ticket = await request_upload(client, user, 100, "image/png")
        ticket["upload"]["fields"]["maxBytes"] = "999999"  # try to raise the signed limit
        assert await post_to_storage(client, ticket, png()) == 403

    async def test_rejects_an_expired_upload_url(self, client: AsyncClient) -> None:
        key, content_type, limit, expires = "uploads/x/y", "image/png", 1000, int(time.time()) - 1
        fields = {
            "key": key,
            "Content-Type": content_type,
            "maxBytes": str(limit),
            "expires": str(expires),
            "signature": sign(
                os.environ["SIGNING_SECRET"], "upload", key, content_type, limit, expires
            ),
        }
        response = await client.post(
            "/storage/upload", data=fields, files={"file": ("f", b"x", "image/png")}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "EXPIRED"

    async def test_rejects_a_file_larger_than_declared(self, client: AsyncClient) -> None:
        user = await create_user(client)
        data = png()
        ticket = await request_upload(client, user, len(data) - 1, "image/png")
        assert await post_to_storage(client, ticket, data) == 413

    async def test_complete_before_uploading_is_a_conflict(self, client: AsyncClient) -> None:
        user = await create_user(client)
        ticket = await request_upload(client, user, 100, "image/png")
        response = await client.post(f"/uploads/{ticket['asset']['id']}/complete", headers=user)
        assert response.status_code == 409

    async def test_validates_the_request(self, client: AsyncClient) -> None:
        user = await create_user(client)
        response = await client.post(
            "/uploads",
            headers=user,
            json={"filename": "", "contentType": "application/pdf", "sizeBytes": 0},
        )
        assert response.status_code == 400
        paths = sorted(d["path"] for d in response.json()["error"]["details"])
        assert paths == ["contentType", "filename", "sizeBytes"]
        too_large = await client.post(
            "/uploads",
            headers=user,
            json={"filename": "a.png", "contentType": "image/png", "sizeBytes": 2_000_001},
        )
        assert too_large.status_code == 413


class TestSignedDownload:
    async def test_expired_or_tampered_download_urls_are_refused(
        self, client: AsyncClient, container: Container
    ) -> None:
        user = await create_user(client)
        await upload(client, user, png(), "image/png")
        storage = container.processing._storage
        key = "uploads/anything"
        expired = storage.presign_download(key, -1)  # type: ignore[attr-defined]
        assert (await client.get(expired)).status_code == 403
        valid = storage.presign_download(key, 60)  # type: ignore[attr-defined]
        assert (await client.get(valid.replace("signature=", "signature=0"))).status_code == 403


class TestCleanup:
    async def test_removes_only_expired_pending_uploads(
        self, client: AsyncClient, container: Container
    ) -> None:
        user = await create_user(client)
        expired = await request_upload(client, user, 100, "image/png")
        assert await post_to_storage(client, expired, png()) == 204  # uploaded, never completed
        fresh = await request_upload(client, user, 100, "image/png")
        _, done = await upload(client, user, png(color=(1, 1, 1)), "image/png")

        async with get_engine().begin() as connection:
            await connection.execute(
                update(Asset)
                .where(Asset.id == expired["asset"]["id"])
                .values(upload_expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )

        expired_key = (await storage_keys(expired["asset"]["id"]))[0]
        assert await container.processing._storage.head(expired_key) is not None
        assert await container.uploads_service.cleanup_expired() == 1
        remaining = {a["id"] for a in (await client.get("/assets", headers=user)).json()["data"]}
        assert remaining == {fresh["asset"]["id"], done["asset"]["id"]}
        assert await container.processing._storage.head(expired_key) is None
