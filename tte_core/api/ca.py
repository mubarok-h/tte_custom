from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ..config import get_settings
from ..pki import PkiError, load_cert

router = APIRouter(tags=["pki"])
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES))


def _fingerprint(path: Path) -> str | None:
    try:
        cert = load_cert(path)
    except PkiError:
        return None
    digest = hashlib.sha256(cert.dump()).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


@router.get("/ca", response_class=HTMLResponse)
def ca_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "ca.html",
        {
            "institution": settings.institution,
            "root_fingerprint": _fingerprint(settings.root_ca_path),
            "intermediate_fingerprint": _fingerprint(
                settings.intermediate_ca_path
            ),
            "root_exists": settings.root_ca_path.exists(),
            "intermediate_exists": settings.intermediate_ca_path.exists(),
        },
    )


@router.get("/ca/root.crt")
def root_certificate() -> FileResponse:
    path = get_settings().root_ca_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Root CA belum tersedia.")
    return FileResponse(
        path,
        media_type="application/x-x509-ca-cert",
        filename="stit-root-ca.crt",
    )


@router.get("/ca/intermediate.crt")
def intermediate_certificate() -> FileResponse:
    path = get_settings().intermediate_ca_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Intermediate CA belum tersedia.")
    return FileResponse(
        path,
        media_type="application/x-x509-ca-cert",
        filename="stit-intermediate-ca.crt",
    )


@router.get("/crl/stit.crl")
def crl() -> FileResponse:
    path = get_settings().crl_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="CRL belum tersedia.")
    return FileResponse(
        path,
        media_type="application/pkix-crl",
        filename="stit.crl",
    )


@router.get("/crl/root.crl")
def root_crl() -> FileResponse:
    path = get_settings().root_crl_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Root CRL belum tersedia.")
    return FileResponse(
        path,
        media_type="application/pkix-crl",
        filename="root.crl",
    )
