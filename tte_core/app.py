from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .admin_auth import is_authenticated
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
            "Layanan tanda tangan elektronik PAdES + QR "
            "STIT Buntet Pesantren Cirebon."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    roles = settings.role_set
    include_all = "all" in roles

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    if include_all or "signer" in roles:
        from .api.signing import router as signing_router

        app.include_router(signing_router)

    if include_all or "signer" in roles or "verifier" in roles:
        from .api.verify_routes import router as verify_router

        app.include_router(verify_router)

    if include_all or "portal" in roles or "verifier" in roles:
        from .api.portal import router as portal_router

        app.include_router(portal_router)

    if include_all or "portal" in roles or "signer" in roles:
        from .api.admin import router as admin_router

        app.include_router(admin_router)

    from .api.letters import router as letters_router

    app.include_router(letters_router)

    from .api.ca import router as ca_router

    app.include_router(ca_router)

    @app.middleware("http")
    async def protect_api_docs(request, call_next):
        if request.url.path in {"/docs", "/redoc", "/openapi.json"} and not is_authenticated(request):
            return RedirectResponse(
                f"/admin/login?next={quote(request.url.path)}", status_code=303
            )
        return await call_next(request)

    @app.middleware("http")
    async def admin_security_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/admin"):
            # SAMEORIGIN (bukan DENY) agar pratinjau surat/kop bisa memakai
            # iframe dari origin yang sama, tanpa membuka framing eksternal.
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            if not request.url.path.startswith("/admin/login"):
                response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app


app = create_app()
