from app.core.config import Settings
from app.core.db import Database
from app.providers.queue.base import JobQueue
from app.providers.queue.postgres import PostgresJobQueue
from app.providers.queue.sqs import SqsJobQueue


# Factory: picks the queue from settings; the uploads module and the worker see the interface.
def create_job_queue(settings: Settings, db: Database) -> JobQueue:
    if settings.job_queue == "sqs":
        # Presence of queue URL and region is checked by Settings validation.
        return SqsJobQueue(settings.sqs_queue_url or "", settings.aws_region or "")
    return PostgresJobQueue(db)
