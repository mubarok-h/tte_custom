from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Mesin tanda tangan elektronik PAdES + QR. "
            "Self-managed CA, non-PSrE / non-AATL."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    roles = settings.role_set
    include_all = "all" in roles

    if include_all or "signer" in roles:
        from .api.signing import router as signing_router

        app.include_router(signing_router)

    if include_all or "signer" in roles or "verifier" in roles:
        from .api.verify_routes import router as verify_router

        app.include_router(verify_router)

    if include_all or "portal" in roles or "verifier" in roles:
        from .api.portal import router as portal_router

        app.include_router(portal_router)

    from .api.ca import router as ca_router

    app.include_router(ca_router)

    return app


app = create_app()
