from __future__ import annotations

from fastapi import Header, HTTPException

from ..config import get_settings


def require_auth(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if settings.require_auth and x_api_key != settings.admin_token:
        raise HTTPException(status_code=401, detail="API key tidak valid.")
