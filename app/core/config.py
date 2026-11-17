from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting comes from environment variables and is validated at startup."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = Field(pattern=r"^postgresql\+asyncpg://")
    admin_api_key: str = Field(min_length=32)

    # Storage (originals and variants)
    storage_provider: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./data/storage"
    public_base_url: str = Field(default="http://localhost:8000", pattern=r"^https?://")
    signing_secret: str = Field(min_length=32)
    s3_bucket: str | None = None
    aws_region: str | None = None

    # Job queue
    job_queue: Literal["postgres", "sqs"] = "postgres"
    sqs_queue_url: str | None = None
    worker_poll_seconds: float = Field(default=1.0, gt=0)

    # Uploads and processing
    max_upload_bytes: int = Field(default=20_000_000, gt=0)
    max_image_pixels: int = Field(default=40_000_000, gt=0)
    default_quota_bytes: int = Field(default=100_000_000, ge=0)
    upload_url_ttl_seconds: int = Field(default=900, gt=0, le=7 * 24 * 3600)
    download_url_ttl_seconds: int = Field(default=300, gt=0, le=7 * 24 * 3600)
    max_processing_attempts: int = Field(default=3, ge=1)
    # NoDecode: read "large:1024,thumbnail:256" with the validator below, not as JSON.
    variant_sizes: Annotated[dict[str, int], NoDecode] = {"large": 1024, "thumbnail": 256}

    @field_validator("variant_sizes", mode="before")
    @classmethod
    def parse_variant_sizes(cls, value: object) -> object:
        """Accepts "large:1024,thumbnail:256" from the environment."""
        if isinstance(value, str):
            pairs = [part.split(":", 1) for part in value.split(",") if part.strip()]
            return {name.strip(): int(size) for name, size in pairs}
        return value

    @model_validator(mode="after")
    def check_provider_settings(self) -> Self:
        problems = []
        if self.storage_provider == "s3" and not (self.s3_bucket and self.aws_region):
            problems.append("STORAGE_PROVIDER=s3 needs S3_BUCKET and AWS_REGION")
        if self.job_queue == "sqs" and not (self.sqs_queue_url and self.aws_region):
            problems.append("JOB_QUEUE=sqs needs SQS_QUEUE_URL and AWS_REGION")
        if "original" in self.variant_sizes:
            problems.append('VARIANT_SIZES cannot use the name "original"')
        if any(size <= 0 for size in self.variant_sizes.values()):
            problems.append("VARIANT_SIZES must be positive")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Singleton: settings are parsed and validated once, then shared."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
