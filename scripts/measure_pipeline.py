"""Measures upload-to-ready time against a running stack (API + worker, local storage).

    docker compose up --build
    uv run python -m scripts.measure_pipeline                 # 50 uploads, 5 at a time
    UPLOADS=200 CONCURRENCY=10 SIZE=2000 uv run python -m scripts.measure_pipeline
    (API_URL and ADMIN_API_KEY point it at another stack.)

Creates a user, then for each upload: builds a random SIZExSIZE JPEG in memory, requests a URL,
posts the file, completes, and polls until the asset is ready or rejected. Prints throughput and
percentiles of the time from the first request to "ready". Every image is different, so dedup
does not shortcut the work. Everything runs on one machine: the numbers describe this setup only.
"""

import asyncio
import os
import statistics
import time
from io import BytesIO

import httpx
from PIL import Image

API_URL = os.environ.get("API_URL", "http://localhost:8000")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "change-me-admin-key-at-least-32-characters")
UPLOADS = int(os.environ.get("UPLOADS", "50"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
SIZE = int(os.environ.get("SIZE", "1200"))


def random_jpeg() -> bytes:
    buffer = BytesIO()
    Image.frombytes("RGB", (SIZE, SIZE), os.urandom(SIZE * SIZE * 3)).save(buffer, "JPEG")
    return buffer.getvalue()


async def one_upload(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[str, float]:
    data = await asyncio.to_thread(random_jpeg)
    started = time.perf_counter()
    ticket = (
        await client.post(
            "/uploads",
            headers=headers,
            json={"filename": "bench.jpg", "contentType": "image/jpeg", "sizeBytes": len(data)},
        )
    ).json()["data"]
    upload = ticket["upload"]
    await client.post(
        upload["url"], data=upload["fields"], files={"file": ("bench.jpg", data, "image/jpeg")}
    )
    asset_id = ticket["asset"]["id"]
    await client.post(f"/uploads/{asset_id}/complete", headers=headers)
    while True:
        status = (await client.get(f"/assets/{asset_id}", headers=headers)).json()["data"]["status"]
        if status in ("ready", "rejected"):
            return status, time.perf_counter() - started
        await asyncio.sleep(0.05)


async def main() -> None:
    async with httpx.AsyncClient(base_url=API_URL, timeout=60) as client:
        created = await client.post(
            "/users",
            headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
            json={"name": "bench", "quotaBytes": 10**12},
        )
        headers = {"Authorization": f"Bearer {created.json()['data']['apiKey']}"}
        results: list[tuple[str, float]] = []
        remaining = UPLOADS
        started = time.perf_counter()

        async def runner() -> None:
            nonlocal remaining
            while remaining > 0:
                remaining -= 1
                results.append(await one_upload(client, headers))

        await asyncio.gather(*(runner() for _ in range(CONCURRENCY)))
        elapsed = time.perf_counter() - started

    times = sorted(t for _, t in results)
    statuses = {s: sum(1 for r, _ in results if r == s) for s in {r for r, _ in results}}
    print(f"uploads: {UPLOADS}, concurrency: {CONCURRENCY}, image: {SIZE}x{SIZE} JPEG")
    print(f"final states: {statuses}")
    print(f"throughput: {UPLOADS / elapsed:.1f} uploads/s")
    print(f"median: {statistics.median(times) * 1000:.0f} ms")
    print(f"p95: {times[min(len(times) - 1, int(0.95 * len(times)))] * 1000:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())
