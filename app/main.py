from fastapi import FastAPI

from app.container import Container, build_container
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.pagination import data_envelope
from app.modules.assets.routes import assets_router
from app.modules.quotas.routes import quotas_router
from app.modules.uploads.local_storage import local_storage_router
from app.modules.uploads.routes import uploads_router
from app.modules.users.routes import users_router
from app.modules.variants.routes import variants_router


def create_app(container: Container | None = None) -> FastAPI:
    configure_logging()
    c = container or build_container(get_settings())

    app = FastAPI(
        title="Media upload pipeline",
        version="1.0.0",
        description="Direct-to-storage image uploads, processed in the background.",
    )
    register_error_handlers(app)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, object]:
        return data_envelope({"status": "ok"})

    app.include_router(users_router(c.users, c.require_admin))
    app.include_router(quotas_router(c.quotas, c.current_user, c.require_admin))
    app.include_router(uploads_router(c.uploads, c.current_user))
    app.include_router(assets_router(c.assets, c.current_user))
    app.include_router(variants_router(c.variants, c.current_user))
    if c.local_storage is not None:
        app.include_router(local_storage_router(c.local_storage))
    return app
