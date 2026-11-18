from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..verify_service import VerificationError, verify_document

router = APIRouter(tags=["portal"])
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES))
templates.env.globals["current_year"] = datetime.now().year


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "institution": settings.institution,
            "verify_base_url": settings.verify_base_url,
        },
    )


@router.get("/v/{short_id}", response_class=HTMLResponse)
def verify_page(
    request: Request,
    short_id: str,
    t: str = Query(default=""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        result = verify_document(db, get_settings(), short_id, t)
        db.commit()
    except VerificationError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "verify.html",
            {
                "institution": get_settings().institution,
                "result": None,
                "error": exc.message,
                "status": "NOT_FOUND" if exc.status_code == 404 else "ERROR",
            },
            status_code=exc.status_code,
        )
    return templates.TemplateResponse(
        request,
        "verify.html",
        {
            "institution": get_settings().institution,
            "result": result,
            "error": None,
            "status": result.get("status", "UNKNOWN"),
        },
    )
