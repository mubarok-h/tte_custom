from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader
from pyhanko.pdf_utils.reader import PdfFileReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import append_event
from ..config import get_settings
from ..db import get_db
from ..models import (
    Document,
    SignatureRecord,
    SignerProfile,
    SignRequest,
    VerificationToken,
    utcnow,
)
from ..signing import SigningError, sign_pdf_bytes
from ..storage import original_path, signed_path, write_bytes
from ..tokens import make_token, new_short_id, sha256_hex, verify_token
from .deps import require_auth
from .schemas import (
    ConsentIn,
    DocumentOut,
    SignRequestIn,
    SignRequestOut,
    SignResult,
)

router = APIRouter(prefix="/api/v1", tags=["signing"])


def _verify_url(short_id: str, token: str) -> str:
    settings = get_settings()
    return f"{settings.verify_base_url}/v/{short_id}?t={token}"


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
        verify_url=_verify_url(doc.short_id, token),
    )


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "tte-stit"}


@router.post("/documents", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> DocumentOut:
    settings = get_settings()
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="File kosong.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Ukuran file melebihi batas.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File bukan PDF.")

    try:
        pages = len(PdfReader(BytesIO(data)).pages)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"PDF tidak valid: {exc}") from exc
    if pages > settings.max_pages:
        raise HTTPException(status_code=400, detail="Jumlah halaman melebihi batas.")

    doc = Document(
        short_id=new_short_id(),
        source_hash=sha256_hex(data),
        original_path="",
        filename=file.filename or "dokumen.pdf",
        size_bytes=len(data),
        status="uploaded",
        retention_until=utcnow() + timedelta(days=365 * settings.retention_years),
    )
    db.add(doc)
    db.flush()

    path = original_path(settings, doc.id)
    write_bytes(path, data)
    doc.original_path = str(path)

    token = make_token(doc.id, settings.token_secret)
    db.add(
        VerificationToken(
            document_id=doc.id,
            token_hash=sha256_hex(token.encode("utf-8")),
        )
    )
    append_event(
        db,
        "document.uploaded",
        {
            "document_id": doc.id,
            "short_id": doc.short_id,
            "sha256": doc.source_hash,
            "filename": doc.filename,
        },
    )
    db.commit()
    db.refresh(doc)
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
    doc = db.get(Document, payload.document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    signer: SignerProfile | None = None
    if payload.signer_id:
        signer = db.get(SignerProfile, payload.signer_id)
        if signer is None:
            raise HTTPException(status_code=404, detail="Penanda tangan tidak ditemukan.")
    elif payload.signer:
        signer = db.execute(
            select(SignerProfile).where(SignerProfile.nip == payload.signer.nip)
        ).scalar_one_or_none()
        if signer is None:
            signer = SignerProfile(
                name=payload.signer.name,
                jabatan=payload.signer.jabatan,
                nip=payload.signer.nip,
                email=payload.signer.email,
            )
            db.add(signer)
            db.flush()
    else:
        raise HTTPException(
            status_code=400, detail="Sertakan signer_id atau data signer."
        )

    req = SignRequest(
        document_id=doc.id,
        signer_id=signer.id,
        reason=payload.reason,
        location=payload.location or get_settings().location,
        status="pending",
    )
    db.add(req)
    append_event(
        db,
        "sign_request.created",
        {
            "document_id": doc.id,
            "signer_id": signer.id,
            "signer_nip": signer.nip,
            "reason": req.reason,
        },
    )
    db.commit()
    db.refresh(req)
    return SignRequestOut.model_validate(req)


@router.post("/sign-requests/{request_id}/consent", response_model=SignRequestOut)
def consent(
    request_id: str,
    payload: ConsentIn,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> SignRequestOut:
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Persetujuan wajib diberikan.")
    req = db.get(SignRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Permintaan tidak ditemukan.")
    if req.status not in ("pending", "consented"):
        raise HTTPException(status_code=409, detail="Status permintaan tidak valid.")

    req.consent_at = utcnow()
    req.consent_ip = request.client.host if request.client else None
    req.status = "consented"
    append_event(
        db,
        "sign_request.consented",
        {
            "sign_request_id": req.id,
            "document_id": req.document_id,
            "signer_id": req.signer_id,
            "ip": req.consent_ip,
        },
    )
    db.commit()
    db.refresh(req)
    return SignRequestOut.model_validate(req)


@router.post("/sign-requests/{request_id}/sign", response_model=SignResult)
def sign(
    request_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
) -> SignResult:
    settings = get_settings()
    req = db.get(SignRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Permintaan tidak ditemukan.")
    if req.status != "consented":
        raise HTTPException(
            status_code=409,
            detail="Permintaan belum disetujui (consent).",
        )

    doc = req.document
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    if doc.status == "signed" and not settings.enable_multisign:
        raise HTTPException(
            status_code=409,
            detail="Dokumen sudah ditandatangani. Multi-tanda tangan nonaktif.",
        )
    if doc.status not in ("uploaded", "signed"):
        raise HTTPException(status_code=409, detail="Status dokumen tidak valid.")

    source_path = (
        Path(doc.signed_path)
        if doc.signed_path and Path(doc.signed_path).exists()
        else Path(doc.original_path)
    )
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Berkas sumber tidak ditemukan.")

    signer = req.signer
    token = make_token(doc.id, settings.token_secret)
    verify_url = _verify_url(doc.short_id, token)
    field_index = len(doc.signatures)

    try:
        signed_bytes, info = sign_pdf_bytes(
            source_path.read_bytes(),
            settings=settings,
            verify_url=verify_url,
            signer_name=signer.name,
            jabatan=signer.jabatan,
            reason=req.reason or "Pengesahan Dokumen Resmi",
            location=req.location or settings.location,
            field_index=field_index,
        )
    except SigningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Gagal menandatangani: {exc}") from exc

    out_path = signed_path(settings, doc.id)
    write_bytes(out_path, signed_bytes)

    serial = None
    subject = None
    try:
        reader = PdfFileReader(BytesIO(signed_bytes))
        cert = reader.embedded_signatures[-1].signer_cert
        if cert is not None:
            serial = str(cert.serial_number)
            subject = str(cert.subject.native)
    except Exception:  # noqa: BLE001
        pass

    doc.signed_path = str(out_path)
    doc.final_hash = sha256_hex(signed_bytes)
    doc.status = "signed"
    req.status = "signed"

    signature = SignatureRecord(
        document_id=doc.id,
        sign_request_id=req.id,
        field_name=info["field_name"],
        cert_serial=serial,
        signed_at=utcnow(),
        # Waktu TSA sebenarnya ada di token RFC 3161 dalam PDF.
        # Kolom ini dapat diisi oleh job validasi arsip.
        tsa_time=None,
        tsa_provider=info.get("tsa_provider"),
        pades_level=info["pades_level"],
        page=0,
    )
    db.add(signature)

    if serial:
        signer.cert_serial = serial
    if subject:
        signer.cert_subject = subject

    append_event(
        db,
        "document.signed",
        {
            "document_id": doc.id,
            "sign_request_id": req.id,
            "signature_id": signature.id,
            "field_name": info["field_name"],
            "pades_level": info["pades_level"],
            "final_sha256": doc.final_hash,
            "cert_serial": serial,
        },
    )
    db.commit()
    db.refresh(signature)

    return SignResult(
        document_id=doc.id,
        signature_id=signature.id,
        field_name=info["field_name"],
        pades_level=info["pades_level"],
        download_url=f"/api/v1/documents/{doc.id}/download?t={token}",
        verify_url=verify_url,
    )



