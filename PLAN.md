# PLAN — media-upload-pipeline

Image uploads that go straight to storage and are processed in the background.

1. A client asks for an upload URL and gets a presigned upload.
2. It sends the file directly to storage (never through the API).
3. It calls "complete".
4. A worker checks the real file type, refuses decompression bombs, strips EXIF/GPS metadata, makes resized variants and records dimensions.

Downloads are short-lived signed URLs. Each user has a storage quota.

**Stack:** Python 3.12 with uv, FastAPI, SQLAlchemy 2 (async) with asyncpg, Alembic, Pydantic v2, Pillow, boto3 (S3/SQS adapters only), pytest with pytest-asyncio, ruff, PostgreSQL 16. No Redis: the job queue is a Postgres table.

## Modules

| Module   | Responsibility                                                                                                                                                                                                                                                                                                   |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| users    | Admin creates users. Each gets an API key, shown once and stored as SHA-256.                                                                                                                                                                                                                                     |
| uploads  | Request an upload (quota check → `pending_upload` asset → presigned POST). Complete an upload (object exists and fits; SHA-256 dedup; quota re-check; status `uploaded`; job enqueued in the same transaction). The local storage endpoints (signed form upload, signed download). Cleanup of abandoned uploads. |
| assets   | List, get and delete assets; signed download URLs; the asset state machine.                                                                                                                                                                                                                                      |
| variants | The image pipeline (magic bytes, pixel limit, metadata stripping, resizing), the processing service the worker runs, and listing an asset's variants.                                                                                                                                                            |
| quotas   | A user's usage and limit; admin sets limits. Usage counts every non-rejected asset (a pending upload counts its declared size, so it is reserved).                                                                                                                                                               |

**Shared (`app/core/`):** settings, logging, errors and handlers, pagination and envelopes, API-model base, auth dependencies (admin key, user key), security helpers (constant-time compare, HMAC signing), database and base repository.

**Providers:**

- `StorageProvider`: local disk (the "presigned" URLs are HMAC-signed URLs to this app's own `/storage/*` endpoints, in the same shape as an S3 presigned POST) or S3 (presigned POST with `content-length-range` and `Content-Type` conditions; presigned GET). The S3 adapter is untested.
- `JobQueue`: Postgres (rows claimed with `FOR UPDATE SKIP LOCKED`, with a lease) or SQS (untested).
- A factory picks each from settings.

**Entrypoints:** `app.main` (API) and `app.worker` (job loop plus `/health`), separate compose services sharing the upload volume. CLI: `python -m scripts.cleanup_uploads`.

## Data model

```
users     id, name, api_key_hash (unique), quota_bytes, created_at
assets    id, owner_id, status, original_filename, declared_content_type, declared_size_bytes, upload_key,
          size_bytes?, checksum_sha256?, format?, width?, height?, rejection_reason?, processing_attempts,
          upload_expires_at, created_at, updated_at, ready_at?
          unique (owner_id, checksum_sha256) WHERE status IN ('uploaded','processing','ready')
variants  id, asset_id, name (original|large|thumbnail), storage_key, content_type, width, height, size_bytes
          unique (asset_id, name)
jobs      id, kind, payload (json), status (queued|running|done|failed), attempts, run_at, locked_until?,
          last_error?, created_at, updated_at
```

## Asset state machine (one explicit table; everything else is rejected)

| From           | Allowed to                                       |
| -------------- | ------------------------------------------------ |
| pending_upload | uploaded                                         |
| uploaded       | processing                                       |
| processing     | ready, rejected, uploaded (retry after an error) |
| ready          | (final)                                          |
| rejected       | (final)                                          |

**Errors vs rejections:**

- **Rejected at once, no retry:** wrong type, too many pixels, file can't be decoded.
- **Retried with backoff:** unexpected errors (e.g. storage unavailable). The asset goes back to `uploaded`, up to `MAX_PROCESSING_ATTEMPTS`, then it is rejected (`processing_failed`).
- **Cleanup:** the CLI deletes `pending_upload` assets whose upload URL expired (and any object already written).

## Pipeline (worker)

1. **Real type from magic bytes** (JPEG, PNG, WebP), never from the extension or header. It must also match the declared content type, so a disguised file is rejected.
2. **Pixel count from the header,** before decoding: width × height > `MAX_IMAGE_PIXELS` → rejected (decompression bomb). Pillow's own bomb guard is set to the same limit.
3. **Re-encode the pixels without EXIF/XMP/text chunks,** after applying the EXIF orientation, so photos stay upright. GPS data is gone.
4. **Variants:** `original` (sanitised, same size), `large` (1024 px) and `thumbnail` (256 px), longest side, never upscaled, same format.
5. The raw upload is deleted, and the asset becomes `ready` with format, width, height and the checksum computed at completion.

**Dedup:** completion streams the object to compute its SHA-256. If the same user already has a non-rejected asset with that checksum, the new upload is deleted and the existing asset is returned. A partial unique index enforces this under races.

**Quota:** checked when the URL is issued (usage + declared size) and again on completion (actual size), each time with the user row locked (`FOR UPDATE`), so concurrent requests can't both pass.

## Endpoints

```
GET    /health
POST   /users                         [admin] returns the API key once
POST   /uploads                       {filename, contentType, sizeBytes} → presigned POST
POST   /uploads/{assetId}/complete    202 queued, or 200 with the existing asset (duplicate)
POST   /storage/upload                local provider only: the signed form upload target
GET    /storage/files/{key}           local provider only: signed download
GET    /assets                        ?status=      GET /assets/{id}     DELETE /assets/{id}
GET    /assets/{id}/variants
GET    /assets/{id}/download          ?variant=original|large|thumbnail → {url, expiresAt}
GET    /quotas/me                     PUT /quotas/{userId} [admin]
```

## Tests (images built in code with Pillow)

- **Integration:**
  - A JPEG with GPS EXIF goes through the full flow; the stored variants have no EXIF, and dimensions are recorded.
  - A disguised file (PDF bytes declared as image/jpeg) is rejected.
  - A hand-built PNG header claiming 30000×30000 is rejected without decoding.
  - The same file uploaded twice by one user returns the existing asset; by two users, two assets.
  - Quota at issue and at completion.
  - Upload-URL signature, expiry and size limits; download URL expiry.
  - A transient error is retried, then rejected after max attempts.
  - Two workers never claim the same job.
  - Cleanup deletes only expired pending uploads.
  - Users only see their own assets.
- **Unit:** state-machine table, magic-byte detection, resize maths, signature helpers, settings, `start_with_secrets`.

## Build order

1. Scaffold. 2. Core and schema. 3. users and quotas. 4. Storage and job-queue providers. 5. uploads. 6. assets and the state machine. 7. variants pipeline and worker. 8. Cleanup CLI. 9. Tests. 10. Deployment files. 11. README.
