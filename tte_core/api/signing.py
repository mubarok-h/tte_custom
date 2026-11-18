from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Document
from ..operations import (
    OperationError,
    create_sign_request as create_sign_request_op,
    grant_consent as grant_consent_op,
    sign_request as sign_request_op,
    upload_document as upload_document_op,
    verify_url_for,
)
from ..tokens import make_token, verify_token
from .deps import require_auth
from .schemas import (
    ConsentIn,
    DocumentOut,
    SignRequestIn,
    SignRequestOut,
    SignResult,
)

router = APIRouter(prefix="/api/v1", tags=["signing"])


def _document_out(doc: Document) -> DocumentOut:
    settings = get_settings()
    token = make_token(doc.id, settings.token_secret)
    return DocumentOut(
        id=doc.id,
        short_id=doc.short_id,
        filename=doc.filename,
        size_bytes=doc.size_bytes,
        status=doc.status,
        source_hash=doc.source_hash,
        final_hash=doc.final_hash,
        created_at=doc.created_at,
        verify_url=verify_url_for(doc.short_id, token),
    )


def _raise_http(exc: OperationError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "tte-stit"}


@router.post("/documents", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> DocumentOut:
    data = await file.read()
    try:
        doc = upload_document_op(
            db,
            data=data,
            filename=file.filename or "dokumen.pdf",
        )
    except OperationError as exc:
        _raise_http(exc)
    return _document_out(doc)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentOut:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    return _document_out(doc)


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    t: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    if not verify_token(doc.id, t, get_settings().token_secret):
        raise HTTPException(status_code=403, detail="Token unduhan tidak valid.")
    path = Path(doc.signed_path) if doc.signed_path else Path(doc.original_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Berkas tidak tersedia.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{Path(doc.filename).stem}-tte.pdf",
    )


@router.post("/sign-requests", response_model=SignRequestOut)
def create_sign_request(
    payload: SignRequestIn,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> SignRequestOut:
    try:
        req = create_sign_request_op(
            db,
            document_id=payload.document_id,
            signer_id=payload.signer_id,
            signer_data=payload.signer.model_dump() if payload.signer else None,
            reason=payload.reason,
            location=payload.location,
        )
    except OperationError as exc:
        _raise_http(exc)
    return SignRequestOut.model_validate(req)


@router.post("/sign-requests/{request_id}/consent", response_model=SignRequestOut)
def consent(
    request_id: str,
    payload: ConsentIn,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> SignRequestOut:
    try:
        req = grant_consent_op(
            db,
            request_id=request_id,
            consent=payload.consent,
            ip=request.client.host if request.client else None,
        )
    except OperationError as exc:
        _raise_http(exc)
    return SignRequestOut.model_validate(req)


@router.post("/sign-requests/{request_id}/sign", response_model=SignResult)
def sign(
    request_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> SignResult:
    try:
        return sign_request_op(db, request_id=request_id)
    except OperationError as exc:
        _raise_http(exc)
