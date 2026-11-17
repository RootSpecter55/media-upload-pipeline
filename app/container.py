from dataclasses import dataclass
from typing import Any

from app.core.auth import make_admin_auth, make_user_auth
from app.core.config import Settings
from app.core.db import Database, get_engine
from app.modules.assets.controller import AssetsController
from app.modules.assets.repository import AssetRepository
from app.modules.assets.service import AssetsService
from app.modules.quotas.controller import QuotasController
from app.modules.quotas.repository import QuotaRepository
from app.modules.quotas.service import QuotasService
from app.modules.uploads.controller import UploadsController
from app.modules.uploads.local_storage import LocalStorageController
from app.modules.uploads.service import UploadsService
from app.modules.users.controller import UsersController
from app.modules.users.repository import UserRepository
from app.modules.users.service import UsersService
from app.modules.variants.controller import VariantsController
from app.modules.variants.repository import VariantRepository
from app.modules.variants.service import ProcessingOptions, ProcessingService, VariantsService
from app.providers.queue.base import JobQueue
from app.providers.queue.factory import create_job_queue
from app.providers.storage.base import StorageProvider
from app.providers.storage.factory import create_storage
from app.providers.storage.local import LocalDiskStorage


@dataclass
class Container:
    require_admin: Any
    current_user: Any
    users: UsersController
    quotas: QuotasController
    uploads: UploadsController
    assets: AssetsController
    variants: VariantsController
    local_storage: LocalStorageController | None
    # Used by the worker and the cleanup CLI.
    queue: JobQueue
    processing: ProcessingService
    uploads_service: UploadsService


def build_container(
    settings: Settings,
    *,
    storage: StorageProvider | None = None,
    queue: JobQueue | None = None,
) -> Container:
    """Composition root: wires every repository, service and controller in one place.
    Providers come from their factories unless passed in (tests pass failing storage)."""
    db = Database(get_engine())
    storage = storage or create_storage(settings)
    queue = queue or create_job_queue(settings, db)
    users_repo = UserRepository()
    assets_repo = AssetRepository()
    variants_repo = VariantRepository()

    users = UsersService(db, users_repo, settings.default_quota_bytes)
    quotas = QuotasService(db, users_repo, QuotaRepository())
    uploads = UploadsService(
        db,
        assets_repo,
        quotas,
        storage,
        queue,
        max_upload_bytes=settings.max_upload_bytes,
        upload_ttl_seconds=settings.upload_url_ttl_seconds,
    )
    processing = ProcessingService(
        db,
        assets_repo,
        variants_repo,
        storage,
        ProcessingOptions(
            max_pixels=settings.max_image_pixels,
            sizes=settings.variant_sizes,
            max_attempts=settings.max_processing_attempts,
        ),
    )

    return Container(
        require_admin=make_admin_auth(settings.admin_api_key),
        current_user=make_user_auth(users.resolve),
        users=UsersController(users),
        quotas=QuotasController(quotas),
        uploads=UploadsController(uploads),
        assets=AssetsController(
            AssetsService(
                db, assets_repo, variants_repo, storage, settings.download_url_ttl_seconds
            )
        ),
        variants=VariantsController(VariantsService(db, assets_repo, variants_repo)),
        local_storage=(
            LocalStorageController(storage) if isinstance(storage, LocalDiskStorage) else None
        ),
        queue=queue,
        processing=processing,
        uploads_service=uploads,
    )
