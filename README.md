# media-upload-pipeline

Image uploads that go straight to storage and are processed in the background.

1. A client asks for an upload URL and receives a **presigned upload**.
2. It sends the file directly to storage; the API never handles the bytes.
3. It confirms the upload.
4. A separate **worker** checks what the file really is, refuses decompression bombs, **strips EXIF/GPS metadata**, makes resized variants and records the dimensions.

Downloads are short-lived signed URLs. Every user has a storage quota, and uploading the same file twice gives back the existing asset.

**What runs where:** everything runs locally with Docker, with no cloud account. Storage is a local directory, and the "presigned" URLs are HMAC-signed URLs to this app's own `/storage/*` endpoints, in exactly the shape of an S3 presigned POST. The job queue is a Postgres table. **S3** and **SQS** adapters sit behind the same interfaces, written against boto3, but are not exercised by the tests. The project is **not deployed**.

**Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, Pydantic v2, Pillow, pytest, ruff, PostgreSQL 16.

## Architecture

```mermaid
sequenceDiagram
  participant C as Client
  participant A as API
  participant S as Storage (local / S3)
  participant DB as PostgreSQL
  participant W as Worker

  C->>A: POST /uploads {filename, contentType, sizeBytes}
  A->>DB: lock user row, check quota, insert asset (pending_upload)
  A-->>C: presigned POST: url + signed fields (key, type, max size, expiry)
  C->>S: POST file (multipart form)
  C->>A: POST /uploads/{id}/complete
  A->>S: head + read object → size, SHA-256
  A->>DB: one transaction: dedup check, quota re-check, status uploaded, insert job
  A-->>C: 202 (or 200 + the existing asset if it is a duplicate)
  W->>DB: claim job (FOR UPDATE SKIP LOCKED, lease)
  W->>S: read raw upload
  W->>W: magic bytes → pixel limit → decode → strip metadata → resize
  W->>S: write variants, delete raw upload
  W->>DB: variants + asset ready (or rejected)
  C->>A: GET /assets/{id}/download?variant=thumbnail
  A-->>C: signed URL, valid for 5 minutes
```

```
app/
  core/        settings, logging, errors, pagination/envelopes, ApiModel, auth (admin key,
               user key), security (constant-time compare, HMAC signing), db, base repository
  providers/   storage/ (local, S3, factory)   queue/ (Postgres, SQS, factory)
  modules/     users, quotas, uploads (+ local storage endpoints), assets (+ state machine),
               variants (+ image pipeline, processing service)
  container.py composition root      main.py  API      worker.py  worker entrypoint
scripts/       start_with_secrets.py (container entrypoint), cleanup_uploads.py (CLI),
               measure_pipeline.py
tests/         images.py (test images built in code), unit/, integration/ (real Postgres)
```

Each module has `routes → controller → service → repository` plus Pydantic `schemas`. The image pipeline (`variants/pipeline.py`) is pure functions on bytes. The worker runs it in a thread, and unit tests call it directly.

## Data model

```mermaid
erDiagram
  users ||--o{ assets : owns
  assets ||--o{ variants : "rendered as"

  users {
    uuid id PK
    string api_key_hash UK "sha256"
    bigint quota_bytes
  }
  assets {
    uuid id PK
    uuid owner_id FK
    string status "pending_upload, uploaded, processing, ready, rejected"
    string declared_content_type "what the client said"
    bigint declared_size_bytes
    string upload_key
    timestamptz upload_expires_at
    bigint size_bytes "what was found"
    string checksum_sha256 "unique per owner among non-rejected"
    string format
    int width
    int height
    string rejection_reason
    int processing_attempts
  }
  variants {
    uuid id PK
    uuid asset_id FK
    string name "original, large, thumbnail"
    string storage_key
    int width
    int height
    bigint size_bytes
  }
  jobs {
    uuid id PK
    string kind
    json payload
    string status "queued, running, done, failed"
    int attempts
    timestamptz run_at
    timestamptz locked_until "lease"
  }
```

## Run it

Requires Docker.

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres (host port **5449**), runs migrations, then starts the **api** on <http://localhost:8000> (OpenAPI docs at `/docs`) and the **worker**. They share a storage volume.

```bash
J='Content-Type: application/json'
KEY=$(curl -s localhost:8000/users -H 'Authorization: Bearer change-me-admin-key-at-least-32-characters' \
  -H "$J" -d '{"name":"Ada"}' | jq -r .data.apiKey)
A="Authorization: Bearer $KEY"

# 1. Ask for an upload URL
TICKET=$(curl -s localhost:8000/uploads -H "$A" -H "$J" \
  -d "{\"filename\":\"photo.jpg\",\"contentType\":\"image/jpeg\",\"sizeBytes\":$(stat -c %s photo.jpg)}")
ID=$(echo $TICKET | jq -r .data.asset.id)

# 2. POST the file straight to storage: every signed field, then the file (same as an S3 presigned POST)
curl -s $(echo $TICKET | jq -r '.data.upload.fields|to_entries|map("-F \(.key)=\(.value)")|join(" ")') \
  -F "file=@photo.jpg;type=image/jpeg" "$(echo $TICKET | jq -r .data.upload.url)"

# 3. Complete; the worker processes it within a second or two
curl -s -X POST localhost:8000/uploads/$ID/complete -H "$A"
curl -s localhost:8000/assets/$ID -H "$A" | jq '.data | {status, width, height}'
curl -s "localhost:8000/assets/$ID/download?variant=thumbnail" -H "$A" | jq -r .data.url
```

Abandoned uploads: `docker compose run --rm api python -m scripts.cleanup_uploads` (run it from a scheduler).

### Tests and lint

```bash
docker compose up -d postgres   # tests use the media_test database, created and migrated automatically
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Every test image is built in code (`tests/images.py`):

- a JPEG with GPS EXIF, a camera make and an orientation tag;
- plain PNGs;
- a PDF declared as `image/jpeg`;
- a hand-assembled PNG whose header claims 30000×30000 pixels.

## Endpoints

Every response uses one envelope: `{ "data": … }`, plus `"meta": { page, pageSize, total, totalPages }` on lists. Errors are `{ "error": { "code", "message", "details"? } }`. Lists take `?page=&pageSize=`. Bodies, query strings and path params are validated by Pydantic. **user** = the user's API key; **admin** = `ADMIN_API_KEY`.

| Method | Path                          | Access        | Notes                                                                                   |
| ------ | ----------------------------- | ------------- | --------------------------------------------------------------------------------------- |
| GET    | `/health`                     | public        |                                                                                         |
| POST   | `/users`                      | admin         | Returns the API key once                                                                |
| POST   | `/uploads`                    | user          | 201 with the presigned POST; 403 `QUOTA_EXCEEDED`; 413 over the size limit              |
| POST   | `/uploads/{assetId}/complete` | user          | 202 queued; 200 if it duplicates an asset you already have; 409 if nothing was uploaded |
| GET    | `/assets` · `/assets/{id}`    | user          | `?status=`; only your own assets                                                        |
| GET    | `/assets/{id}/variants`       | user          |                                                                                         |
| GET    | `/assets/{id}/download`       | user          | `?variant=original\|large\|thumbnail` → `{url, expiresAt}`; 409 unless ready            |
| DELETE | `/assets/{id}`                | user          | Deletes the files too; 409 while processing                                             |
| GET    | `/quotas/me`                  | user          | `{quotaBytes, usedBytes, availableBytes}`                                               |
| PUT    | `/quotas/{userId}`            | admin         |                                                                                         |
| POST   | `/storage/upload`             | signed fields | Local provider only: the presigned POST target                                          |
| GET    | `/storage/files/{key}`        | signed URL    | Local provider only                                                                     |

## Key design decisions

**Upload straight to storage.** The API only issues a presigned POST, whose signed policy fixes three things:

- **The object key.** The client cannot choose where the file lands.
- **The content type.**
- **The maximum size.** S3 enforces it with `content-length-range`.

That keeps large files off the API servers. The local provider imitates this exactly: same POST, same field names, an HMAC instead of an AWS signature, so client code and tests are identical for both.

**Never trust the client's description of the file.** The declared type and size are only used to reserve quota and to sign the upload. The worker:

1. **Reads the type from magic bytes** (`FF D8 FF` for JPEG, the 8-byte PNG signature, `RIFF….WEBP`), and rejects anything else. That rejects a PDF called `photo.jpg`, and also a real PNG declared as JPEG, so a file can't be mislabelled into a different processing path.
2. **Reads width × height from the header and compares it with `MAX_IMAGE_PIXELS`** before decoding anything. A decompression bomb (a tiny file that decodes to gigabytes) is refused without allocating its pixels; the test bomb is under 200 bytes and claims 900 million pixels. Pillow's own guard is a second line of defence.
3. **Strips metadata by rebuilding the image from its pixels** (`Image.frombytes`), after applying the EXIF orientation so photos stay upright. EXIF (GPS, camera, timestamps), XMP and text chunks are gone; only the ICC colour profile is kept. The raw upload is deleted once the variants exist, so the GPS data is not kept anywhere. The test downloads the stored original and finds zero EXIF tags.

**An explicit state machine for the asset lifecycle** (`assets/state.py`):

| From           | Allowed to                                       |
| -------------- | ------------------------------------------------ |
| pending_upload | uploaded                                         |
| uploaded       | processing                                       |
| processing     | ready, rejected, uploaded (retry after an error) |

`ready` and `rejected` are final. Every move goes through `transition()`, under a row lock. That also makes the worker safe to run twice for the same job, since at-least-once delivery means it might be: a finished asset is skipped, and an asset left in `processing` by a dead worker takes the retry path.

**Rejections are final; errors are retried.** A wrong type, too many pixels or an undecodable file can't get better, so they are rejected immediately. Anything unexpected (storage unavailable, a crash) sends the asset back to `uploaded` and reschedules the job with exponential backoff (10 s, 20 s, …). At `MAX_PROCESSING_ATTEMPTS` the asset is rejected with `processing_failed: …`.

**The queue is a Postgres table, claimed with SKIP LOCKED.** `PostgresJobQueue.claim()` selects the oldest due job `FOR UPDATE SKIP LOCKED`: a second worker skips the locked row instead of waiting or taking it too. Claiming sets a lease (`locked_until`); a job whose worker died becomes claimable again when the lease runs out.

Because the queue is a table, **completing an upload and enqueuing its job happen in one transaction**: no uploaded asset without a job, no job for an asset that never committed. The SQS adapter can't offer that, so it sends immediately, and the state machine makes a stray job harmless (documented in the class).

**Dedup by content, enforced by the database.** Completion streams the object and computes its SHA-256. If the same user already has a non-rejected asset with that hash, the new upload is deleted and the existing asset is returned (200, `duplicate: true`). A partial unique index on `(owner_id, checksum_sha256) WHERE status IN ('uploaded','processing','ready')` settles the race where both copies complete at once.

**Quota checked twice, under a lock.**

- **When the URL is issued,** usage plus the declared size must fit. A pending upload counts its declared size, so reservations can't be oversubscribed.
- **On completion,** the check runs again with the real size. If the quota was lowered in between, the upload is discarded.

Both checks lock the user row (`SELECT … FOR UPDATE`), so parallel requests from one user are checked one at a time.

**Patterns, only where they earn their place.**

- _Singleton:_ settings and the database engine.
- _Factory:_ `StorageProvider` (local or S3) and `JobQueue` (Postgres or SQS).
- _Repository:_ all data access.
- _State machine:_ the asset lifecycle.

## Results

Not yet measured, run `uv run python -m scripts.measure_pipeline`.

## Known limitations

- **Completion reads the whole object** to hash it (at most `MAX_UPLOAD_BYTES`, 20 MB by default). With S3, a client-supplied `x-amz-checksum-sha256` checked by S3 would avoid the download.
- **The local `/storage/upload` endpoint is parsed fully by Starlette before the size check runs.** S3 rejects oversized uploads at the edge, so this only affects local use.
- **Only JPEG, PNG and WebP.** No animated images (only the first frame would be kept), no HEIC, and no colour-space conversion beyond carrying the ICC profile.
- **Variants use the source format.** There is no WebP/AVIF conversion and no `srcset`-style sizing.
- **Quota counts originals only,** not variants.
- **The SQS and S3 adapters are not tested,** and the SQS adapter can't enqueue inside the database transaction. Visibility timeout and backoff are capped at SQS's 12 hours.
- **The cleanup CLI must be scheduled externally.** Nothing runs it automatically.
- **Users authenticate with static API keys,** created by an admin. There is no key rotation endpoint.

## Deployment

| File                                     | Purpose                                                                                                                                                                                                                                                                                              |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Dockerfile`                             | Multi-stage build with uv: locked production dependencies in a virtualenv, then a slim runtime image running as the non-root `app` user, with a healthcheck on `/health`. The same image runs the API and, with the `worker` argument, the worker.                                                   |
| `scripts/start_with_secrets.py`          | Container entrypoint. With `SECRETS_SOURCE=aws` it loads one JSON secret (`SECRET_ID`) from AWS Secrets Manager into the environment (it must contain `DATABASE_URL`, `ADMIN_API_KEY`, `SIGNING_SECRET`); with `local` it uses `.env`. It validates the settings, then starts the API or the worker. |
| `deploy/ecs-task-definition.json`        | ECS Fargate task definition template for the API, with `${…}` placeholders for account id, region, image, role ARNs, secret id, bucket, queue URL and public URL. It selects `STORAGE_PROVIDER=s3` and `JOB_QUEUE=sqs`.                                                                              |
| `deploy/ecs-task-definition.worker.json` | The same for the worker, as a separate service with more memory for image decoding.                                                                                                                                                                                                                  |
| `.github/workflows/deploy.yml`           | Manual-only (`workflow_dispatch`) workflow. It assumes an AWS role through GitHub OIDC (no stored keys), builds and pushes the image to ECR, fills in both task definitions with `envsubst` and updates both ECS services.                                                                           |
| `.github/workflows/ci.yml`               | Runs ruff (lint and format check) and pytest against a Postgres service container on every push and pull request.                                                                                                                                                                                    |

Deployment config is provided as a reference. This project has not been deployed.
