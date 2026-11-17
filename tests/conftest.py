"""Test configuration. The environment is set before any app module reads settings."""

import os
import tempfile

os.environ.update(
    {
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5449/media_test"
        ),
        "ADMIN_API_KEY": "test-admin-key-that-is-at-least-32-characters",
        "SIGNING_SECRET": "test-signing-secret-that-is-at-least-32-chars",
        "STORAGE_PROVIDER": "local",
        "LOCAL_STORAGE_DIR": tempfile.mkdtemp(prefix="media-test-storage-"),
        # The test client's base URL, so signed URLs point back at the app under test.
        "PUBLIC_BASE_URL": "http://test",
        "JOB_QUEUE": "postgres",
        "MAX_UPLOAD_BYTES": "2000000",
        "MAX_IMAGE_PIXELS": "4000000",
        "DEFAULT_QUOTA_BYTES": "5000000",
        "MAX_PROCESSING_ATTEMPTS": "3",
        # Small variants keep the tests fast.
        "VARIANT_SIZES": "large:64,thumbnail:16",
    }
)
