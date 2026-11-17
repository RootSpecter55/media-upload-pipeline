"""Deletes uploads that were requested but never completed, once their upload URL has expired.

python -m scripts.cleanup_uploads            # run from cron or a scheduled task, e.g. hourly
"""

import asyncio

from dotenv import load_dotenv

from app.container import build_container
from app.core.config import get_settings
from app.core.db import get_engine


async def main() -> None:
    load_dotenv()  # never overrides variables that are already set
    container = build_container(get_settings())
    removed = await container.uploads_service.cleanup_expired()
    print(f"Removed {removed} abandoned upload(s)")
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
