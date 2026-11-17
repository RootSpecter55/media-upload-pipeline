import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.providers.storage.base import ObjectInfo, PresignedUpload


class S3Storage:
    """One S3 bucket. The client uploads straight to S3 with a presigned POST whose policy fixes
    the key and content type and caps the size; downloads are presigned GETs. boto3 is
    synchronous, so network calls run in a worker thread (presigning is local and instant).

    Written against the official SDK but not exercised by the tests: it needs a real bucket.
    """

    name = "s3"

    def __init__(self, bucket: str, region: str, client: Any = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3", region_name=region)

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload:
        post = self._client.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=expires_in,
        )
        return PresignedUpload(
            url=post["url"],
            fields=post["fields"],
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    def presign_download(self, key: str, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_in
        )

    async def head(self, key: str) -> ObjectInfo | None:
        try:
            response = await asyncio.to_thread(
                self._client.head_object, Bucket=self._bucket, Key=key
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectInfo(
            size_bytes=response["ContentLength"],
            content_type=response.get("ContentType", "application/octet-stream"),
        )

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
