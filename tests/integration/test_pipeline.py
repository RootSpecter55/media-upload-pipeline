import hashlib
from io import BytesIO

from httpx import AsyncClient
from PIL import ExifTags, Image
from sqlalchemy import func, select

from app.container import Container
from app.core.db import get_engine
from app.modules.assets.models import Asset
from app.providers.queue.models import JobRow
from tests.images import disguised_pdf, jpeg_with_gps, png, png_bomb
from tests.integration.conftest import create_user, drain, storage_keys, upload


async def _count(model: type) -> int:
    async with get_engine().connect() as connection:
        return int(await connection.scalar(select(func.count()).select_from(model)) or 0)


async def _download(
    client: AsyncClient, user: dict[str, str], asset_id: str, variant: str
) -> bytes:
    signed = await client.get(f"/assets/{asset_id}/download?variant={variant}", headers=user)
    assert signed.status_code == 200, signed.text
    response = await client.get(signed.json()["data"]["url"])
    assert response.status_code == 200
    return response.content


class TestFullFlow:
    async def test_phone_photo_is_sanitised_resized_and_recorded(
        self, client: AsyncClient, container: Container
    ) -> None:
        user = await create_user(client)
        photo = jpeg_with_gps(40, 20)
        # The test image really carries GPS data and a camera make.
        exif = Image.open(BytesIO(photo)).getexif()
        assert exif.get_ifd(ExifTags.IFD.GPSInfo) and exif[ExifTags.Base.Make] == "TestPhone"

        status, completed = await upload(client, user, photo)
        assert status == 202
        assert completed["asset"]["status"] == "uploaded"
        assert completed["asset"]["checksumSha256"] == hashlib.sha256(photo).hexdigest()
        assert await _count(JobRow) == 1  # enqueued in the same transaction

        await drain(container)

        asset = (await client.get(f"/assets/{completed['asset']['id']}", headers=user)).json()
        data = asset["data"]
        assert data["status"] == "ready"
        # The orientation tag said "rotate 90": the stored image is upright, 20 wide x 40 high.
        assert (data["format"], data["width"], data["height"]) == ("jpeg", 20, 40)

        original = Image.open(BytesIO(await _download(client, user, data["id"], "original")))
        assert original.size == (20, 40)
        assert not original.getexif()  # no EXIF at all: GPS, make and orientation are gone
        assert "exif" not in original.info

        variants = (await client.get(f"/assets/{data['id']}/variants", headers=user)).json()
        sizes = {v["name"]: (v["width"], v["height"]) for v in variants["data"]}
        assert sizes == {"original": (20, 40), "large": (20, 40), "thumbnail": (8, 16)}
        thumbnail = Image.open(BytesIO(await _download(client, user, data["id"], "thumbnail")))
        assert thumbnail.size == (8, 16)

        # The raw upload (which still had GPS) is gone from storage; the variants are there.
        storage = container.processing._storage
        upload_key, *variant_keys = await storage_keys(data["id"])
        assert await storage.head(upload_key) is None
        assert len(variant_keys) == 3
        for key in variant_keys:
            assert await storage.head(key) is not None

    async def test_png_keeps_its_format(self, client: AsyncClient, container: Container) -> None:
        user = await create_user(client)
        _, completed = await upload(client, user, png(100, 50), "image/png")
        await drain(container)
        asset = (await client.get(f"/assets/{completed['asset']['id']}", headers=user)).json()
        assert asset["data"]["format"] == "png"
        variants = (
            await client.get(f"/assets/{completed['asset']['id']}/variants", headers=user)
        ).json()
        large = next(v for v in variants["data"] if v["name"] == "large")
        assert (large["width"], large["height"], large["contentType"]) == (64, 32, "image/png")


class TestRejections:
    async def _rejection(
        self, client: AsyncClient, container: Container, data: bytes, content_type: str
    ) -> str:
        user = await create_user(client)
        _, completed = await upload(client, user, data, content_type)
        await drain(container)
        asset = (await client.get(f"/assets/{completed['asset']['id']}", headers=user)).json()
        assert asset["data"]["status"] == "rejected"
        assert await _count(Asset) == 1
        return asset["data"]["rejectionReason"]

    async def test_disguised_file_is_rejected_by_its_magic_bytes(
        self, client: AsyncClient, container: Container
    ) -> None:
        reason = await self._rejection(client, container, disguised_pdf(), "image/jpeg")
        assert reason.startswith("unsupported_type")

    async def test_real_image_with_the_wrong_declared_type_is_rejected(
        self, client: AsyncClient, container: Container
    ) -> None:
        reason = await self._rejection(client, container, png(), "image/jpeg")
        assert reason == "type_mismatch: declared image/jpeg but the file is image/png"

    async def test_decompression_bomb_is_rejected_from_its_header(
        self, client: AsyncClient, container: Container
    ) -> None:
        bomb = png_bomb()
        assert len(bomb) < 200  # tiny on disk...
        reason = await self._rejection(client, container, bomb, "image/png")
        assert reason.startswith("too_many_pixels")  # ...900 million pixels if decoded

    async def test_a_rejected_asset_cannot_be_downloaded(
        self, client: AsyncClient, container: Container
    ) -> None:
        user = await create_user(client)
        _, completed = await upload(client, user, disguised_pdf(), "image/jpeg")
        await drain(container)
        response = await client.get(f"/assets/{completed['asset']['id']}/download", headers=user)
        assert response.status_code == 409


class TestDeduplication:
    async def test_same_user_same_file_gets_the_existing_asset(
        self, client: AsyncClient, container: Container
    ) -> None:
        user = await create_user(client)
        photo = jpeg_with_gps()
        _, first = await upload(client, user, photo)
        await drain(container)

        status, second = await upload(client, user, photo)
        assert status == 200
        assert second["duplicate"] is True
        assert second["asset"]["id"] == first["asset"]["id"]
        assert second["asset"]["status"] == "ready"
        assert await _count(Asset) == 1

    async def test_duplicate_detected_while_the_first_is_still_queued(
        self, client: AsyncClient
    ) -> None:
        user = await create_user(client)
        photo = png()
        _, first = await upload(client, user, photo, "image/png")
        _, second = await upload(client, user, photo, "image/png")
        assert second == {"asset": first["asset"], "duplicate": True}

    async def test_different_users_each_get_their_own_asset(self, client: AsyncClient) -> None:
        photo = png()
        _, a = await upload(client, await create_user(client), photo, "image/png")
        _, b = await upload(client, await create_user(client), photo, "image/png")
        assert a["asset"]["id"] != b["asset"]["id"]
        assert not b["duplicate"]


class TestOwnership:
    async def test_users_only_see_their_own_assets(self, client: AsyncClient) -> None:
        alice, bob = await create_user(client), await create_user(client)
        _, completed = await upload(client, alice, png(), "image/png")
        asset_id = completed["asset"]["id"]
        assert (await client.get(f"/assets/{asset_id}", headers=bob)).status_code == 404
        assert (await client.post(f"/uploads/{asset_id}/complete", headers=bob)).status_code == 404
        listing = await client.get("/assets", headers=bob)
        assert listing.json()["meta"]["total"] == 0
        assert (
            await client.get("/assets", headers={"Authorization": "Bearer nope"})
        ).status_code == 401

    async def test_list_paginates_and_delete_removes_files(
        self, client: AsyncClient, container: Container
    ) -> None:
        user = await create_user(client)
        for color in ((1, 2, 3), (4, 5, 6), (7, 8, 9)):
            await upload(client, user, png(color=color), "image/png")
        await drain(container)
        page = (await client.get("/assets?pageSize=2&status=ready", headers=user)).json()
        assert page["meta"] == {"page": 1, "pageSize": 2, "total": 3, "totalPages": 2}

        asset_id = page["data"][0]["id"]
        _, *variant_keys = await storage_keys(asset_id)
        storage = container.processing._storage
        assert all([await storage.head(key) is not None for key in variant_keys])
        assert (await client.delete(f"/assets/{asset_id}", headers=user)).status_code == 204
        for key in variant_keys:
            assert await storage.head(key) is None  # every file is gone with the asset
        assert (await client.get(f"/assets/{asset_id}", headers=user)).status_code == 404
