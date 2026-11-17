import json
from io import BytesIO
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from PIL import Image
from pydantic import ValidationError

from app.core.config import Settings
from app.core.security import sign, signature_valid
from app.modules.assets.models import Asset, AssetStatus
from app.modules.assets.state import ALLOWED_TRANSITIONS, InvalidTransitionError, transition
from app.modules.variants.pipeline import (
    ImageRejectedError,
    detect_format,
    fit_within,
    process_image,
)
from scripts.start_with_secrets import SecretsError, load_secrets, parse_role
from tests.images import disguised_pdf, jpeg_with_gps, png, png_bomb

SIZES = {"large": 64, "thumbnail": 16}


class TestStateMachine:
    def test_allows_exactly_the_listed_transitions(self) -> None:
        allowed = {
            (current.value, target.value)
            for current, targets in ALLOWED_TRANSITIONS.items()
            for target in targets
        }
        assert allowed == {
            ("pending_upload", "uploaded"),
            ("uploaded", "processing"),
            ("processing", "ready"),
            ("processing", "rejected"),
            ("processing", "uploaded"),
        }
        assert ALLOWED_TRANSITIONS[AssetStatus.READY] == frozenset()
        assert ALLOWED_TRANSITIONS[AssetStatus.REJECTED] == frozenset()

    def test_transition_moves_or_refuses(self) -> None:
        asset = Asset(status=AssetStatus.UPLOADED)
        transition(asset, AssetStatus.PROCESSING)
        assert asset.status == "processing"
        with pytest.raises(InvalidTransitionError, match="from processing to pending_upload"):
            transition(asset, AssetStatus.PENDING_UPLOAD)
        asset.status = AssetStatus.READY
        with pytest.raises(InvalidTransitionError):
            transition(asset, AssetStatus.PROCESSING)


class TestPipeline:
    def test_detects_formats_from_magic_bytes_only(self) -> None:
        assert detect_format(jpeg_with_gps()) == "jpeg"
        assert detect_format(png()) == "png"
        buffer = BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "WEBP")
        assert detect_format(buffer.getvalue()) == "webp"
        assert detect_format(disguised_pdf()) is None
        assert detect_format(b"GIF89a....") is None

    @pytest.mark.parametrize(
        ("size", "longest", "expected"),
        [
            ((4000, 3000), 1024, (1024, 768)),
            ((3000, 4000), 256, (192, 256)),
            ((100, 50), 1024, (100, 50)),  # never upscaled
            ((5000, 1), 256, (256, 1)),  # never zero
        ],
    )
    def test_fit_within_keeps_aspect_ratio(
        self, size: tuple[int, int], longest: int, expected: tuple[int, int]
    ) -> None:
        assert fit_within(*size, longest) == expected

    def test_strips_metadata_and_applies_orientation(self) -> None:
        result = process_image(jpeg_with_gps(40, 20), "image/jpeg", 1_000_000, SIZES)
        assert (result.format, result.width, result.height) == ("jpeg", 20, 40)
        for variant in result.variants:
            image = Image.open(BytesIO(variant.data))
            assert not image.getexif()
            assert (image.width, image.height) == (variant.width, variant.height)
        assert [v.name for v in result.variants] == ["original", "large", "thumbnail"]

    def test_checks_pixels_before_decoding(self) -> None:
        with pytest.raises(ImageRejectedError, match="too_many_pixels"):
            process_image(png(100, 100), "image/png", 9_999, SIZES)
        with pytest.raises(ImageRejectedError, match="too_many_pixels"):
            process_image(png_bomb(), "image/png", 40_000_000, SIZES)

    def test_rejects_truncated_images(self) -> None:
        with pytest.raises(ImageRejectedError, match="undecodable"):
            process_image(png()[:60], "image/png", 1_000_000, SIZES)


class TestSigning:
    def test_signature_covers_every_part(self) -> None:
        signature = sign("secret", "upload", "key", "image/png", 100, 1700000000)
        assert signature_valid("secret", signature, "upload", "key", "image/png", 100, 1700000000)
        assert not signature_valid(
            "secret", signature, "upload", "key", "image/png", 101, 1700000000
        )
        assert not signature_valid(
            "other", signature, "upload", "key", "image/png", 100, 1700000000
        )


class TestSettings:
    BASE: ClassVar[dict[str, Any]] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "admin_api_key": "x" * 32,
        "signing_secret": "y" * 32,
    }

    def test_parses_variant_sizes(self) -> None:
        settings = Settings(**{**self.BASE, "variant_sizes": "medium:512,tiny:32"})
        assert settings.variant_sizes == {"medium": 512, "tiny": 32}

    def test_rejects_incomplete_provider_settings(self) -> None:
        with pytest.raises(ValidationError, match="STORAGE_PROVIDER=s3 needs S3_BUCKET"):
            Settings(**{**self.BASE, "storage_provider": "s3"})
        with pytest.raises(ValidationError, match="JOB_QUEUE=sqs needs SQS_QUEUE_URL"):
            Settings(**{**self.BASE, "job_queue": "sqs", "aws_region": "eu-west-1"})
        with pytest.raises(ValidationError, match='cannot use the name "original"'):
            Settings(**{**self.BASE, "variant_sizes": "original:100"})

    def test_requires_long_secrets(self) -> None:
        with pytest.raises(ValidationError, match="signing_secret"):
            Settings(**{**self.BASE, "signing_secret": "short"})


def _client_returning(secret: Any) -> MagicMock:
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": secret}
    return client


COMPLETE = {"DATABASE_URL": "postgresql+asyncpg://x", "ADMIN_API_KEY": "a", "SIGNING_SECRET": "s"}


class TestStartWithSecrets:
    def test_copies_every_key_of_the_aws_secret(self) -> None:
        env = {"SECRETS_SOURCE": "aws", "SECRET_ID": "app/prod"}
        client = _client_returning(json.dumps({**COMPLETE, "EXTRA": 1}))
        load_secrets(env, client)
        assert env["EXTRA"] == "1" and env["SIGNING_SECRET"] == "s"
        client.get_secret_value.assert_called_once_with(SecretId="app/prod")

    def test_fails_fast_on_missing_id_secret_or_keys(self) -> None:
        with pytest.raises(SecretsError, match="SECRET_ID is required"):
            load_secrets({"SECRETS_SOURCE": "aws"}, _client_returning("{}"))
        failing = MagicMock()
        failing.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}}, "GetSecretValue"
        )
        with pytest.raises(SecretsError, match='Could not read secret "x"'):
            load_secrets({"SECRETS_SOURCE": "aws", "SECRET_ID": "x"}, failing)
        with pytest.raises(SecretsError, match="missing required keys: ADMIN_API_KEY, SIGNING"):
            load_secrets(
                {"SECRETS_SOURCE": "aws", "SECRET_ID": "x"},
                _client_returning(json.dumps({"DATABASE_URL": "d"})),
            )
        with pytest.raises(SecretsError, match="not valid JSON"):
            load_secrets({"SECRETS_SOURCE": "aws", "SECRET_ID": "x"}, _client_returning("nope"))

    def test_local_source_never_calls_aws(self) -> None:
        client = _client_returning("{}")
        load_secrets({"SECRETS_SOURCE": "local"}, client)
        client.get_secret_value.assert_not_called()
        with pytest.raises(SecretsError, match='must be "local" or "aws"'):
            load_secrets({"SECRETS_SOURCE": "vault"})

    def test_parses_the_role(self) -> None:
        assert parse_role(["prog"]) == "api"
        assert parse_role(["prog", "worker"]) == "worker"
        with pytest.raises(SecretsError, match='got "cron"'):
            parse_role(["prog", "cron"])
