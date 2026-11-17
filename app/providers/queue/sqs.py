import asyncio
import json
from typing import Any

import boto3
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.queue.base import Job


class SqsJobQueue:
    """Amazon SQS. A received message is hidden for the queue's visibility timeout; deleting it
    completes the job, and changing its visibility delays the retry.

    Unlike the Postgres queue, the message is sent immediately, not with the caller's
    transaction: if that transaction rolls back, the worker finds the asset not in `uploaded`
    and skips the job. Written against the official SDK but not exercised by the tests.
    """

    name = "sqs"

    def __init__(self, queue_url: str, region: str, client: Any = None) -> None:
        self._queue_url = queue_url
        self._client = client or boto3.client("sqs", region_name=region)

    async def enqueue(self, session: AsyncSession, kind: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"kind": kind, "payload": payload})
        await asyncio.to_thread(
            self._client.send_message, QueueUrl=self._queue_url, MessageBody=body
        )

    async def claim(self) -> Job | None:
        response = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        body = json.loads(message["Body"])
        return Job(
            id=message["MessageId"],
            kind=body["kind"],
            payload=body["payload"],
            attempts=int(message.get("Attributes", {}).get("ApproximateReceiveCount", 1)),
            receipt=message["ReceiptHandle"],
        )

    async def complete(self, job: Job) -> None:
        await asyncio.to_thread(
            self._client.delete_message, QueueUrl=self._queue_url, ReceiptHandle=job.receipt
        )

    async def retry(self, job: Job, delay_seconds: float, error: str) -> None:
        await asyncio.to_thread(
            self._client.change_message_visibility,
            QueueUrl=self._queue_url,
            ReceiptHandle=job.receipt,
            VisibilityTimeout=min(int(delay_seconds), 12 * 3600),  # SQS maximum
        )
