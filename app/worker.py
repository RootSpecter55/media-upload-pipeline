"""Worker entrypoint: claims processing jobs and runs the image pipeline. Run as many as needed;
the queue hands each job to one worker at a time. Serves only GET /health."""

import asyncio
import logging
import os
import signal
import time
from uuid import UUID

from app.container import Container, build_container
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import configure_logging
from app.modules.uploads.service import PROCESS_ASSET
from app.modules.variants.service import RetryLaterError

logger = logging.getLogger("app.worker")

# If the loop has not finished an iteration for this long, /health fails.
STALE_AFTER_SECONDS = 120


async def run_one(container: Container) -> bool:
    """Claims and handles one job. Returns False when there was nothing to do."""
    job = await container.queue.claim()
    if job is None:
        return False
    if job.kind != PROCESS_ASSET:
        logger.error("Unknown job kind %s; dropping it", job.kind)
        await container.queue.complete(job)
        return True
    try:
        outcome = await container.processing.process(UUID(job.payload["assetId"]))
        logger.info("Asset %s: %s", job.payload["assetId"], outcome)
        await container.queue.complete(job)
    except RetryLaterError as exc:
        await container.queue.retry(job, exc.delay_seconds, str(exc))
    return True


async def serve_health(port: int, last_tick: list[float]) -> asyncio.Server:
    """A minimal HTTP responder, so the same container healthcheck works for API and worker."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        healthy = time.monotonic() - last_tick[0] < STALE_AFTER_SECONDS
        ok = request_line.startswith(b"GET /health ") and healthy
        body = b'{"data":{"status":"ok"}}' if ok else b'{"error":{"code":"UNHEALTHY"}}'
        status = b"200 OK" if ok else b"503 Service Unavailable"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "0.0.0.0", port)


async def main() -> None:
    configure_logging()
    settings = get_settings()
    container = build_container(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    last_tick = [time.monotonic()]
    server = await serve_health(int(os.environ.get("PORT", "8000")), last_tick)
    logger.info("Worker started (queue: %s)", container.queue.name)
    while not stop.is_set():
        try:
            worked = await run_one(container)
            last_tick[0] = time.monotonic()
            if not worked:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            pass
        except Exception:
            logger.exception("Worker iteration failed")
            await asyncio.sleep(1)
    server.close()
    await get_engine().dispose()
    logger.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
