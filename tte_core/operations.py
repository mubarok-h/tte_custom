from __future__ import annotations

"""Operasi inti yang dipakai bersama oleh API dan panel admin.

Modul ini memisahkan logika bisnis (validasi upload, pembuatan permintaan
tanda tangan, consent, signing) dari lapisan HTTP supaya API dan panel admin
berperilaku konsisten.
"""

from datetime import timedelta
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pyhanko.pdf_utils.reader import PdfFileReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from .api.schemas import SignResult
from .audit import append_event
from .config import Settings, get_settings
from .models import (
    Document,
    LetterRequest,
    SignatureRecord,
    SignerProfile,
    SignRequest,
    VerificationToken,
    utcnow,
)
from .pki import PkiError, load_signer_for_profile
from .signing import SigningError, sign_pdf_bytes
from .storage import original_path, signed_path, write_bytes
from .tokens import make_token, new_short_id, sha256_hex


class OperationError(Exception):
    """Kesalahan domain yang dapat dipetakan ke HTTPException."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def verify_url_for(short_id: str, token: str) -> str:
    settings = get_settings()
    return f"{settings.verify_base_url}/v/{short_id}?t={token}"


def upload_document(
    db: Session,
    *,
    data: bytes,
    filename: str,
    settings: Settings | None = None,
) -> Document:
    settings = settings or get_settings()
    if len(data) == 0:
        raise OperationError("File kosong.", 400)
    if len(data) > settings.max_upload_bytes:
        raise OperationError("Ukuran file melebihi batas.", 413)
    if not data.startswith(b"%PDF"):
        raise OperationError("File bukan PDF.", 400)

    try:
        pages = len(PdfReader(BytesIO(data)).pages)
    except Exception as exc:  # noqa: BLE001
        raise OperationError(f"PDF tidak valid: {exc}", 400) from exc
    if pages > settings.max_pages:
        raise OperationError("Jumlah halaman melebihi batas.", 400)

    doc = Document(
        short_id=new_short_id(),
        source_hash=sha256_hex(data),
        original_path="",
        filename=filename or "dokumen.pdf",
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
    return doc


def create_sign_request(
    db: Session,
    *,
    document_id: str,
    signer_id: str | None = None,
    signer_data: dict | None = None,
    reason: str | None = None,
    location: str | None = None,
    settings: Settings | None = None,
) -> SignRequest:
    settings = settings or get_settings()
    doc = db.get(Document, document_id)
    if doc is None:
        raise OperationError("Dokumen tidak ditemukan.", 404)

    signer: SignerProfile | None = None
    if signer_id:
        signer = db.get(SignerProfile, signer_id)
        if signer is None:
            raise OperationError("Penanda tangan tidak ditemukan.", 404)
    elif signer_data:
        nip = (signer_data.get("nip") or "").strip()
        signer = db.execute(
            select(SignerProfile).where(SignerProfile.nip == nip).limit(1)
        ).scalar_one_or_none()
        if signer is None:
            signer = SignerProfile(
                name=signer_data.get("name") or "",
                jabatan=signer_data.get("jabatan") or "",
                nip=nip,
                email=signer_data.get("email"),
            )
            db.add(signer)
            db.flush()
    else:
        raise OperationError("Sertakan signer_id atau data signer.", 400)

    req = SignRequest(
        document_id=doc.id,
        signer_id=signer.id,
        reason=reason or "Pengesahan Dokumen Resmi",
        location=location or settings.location,
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
    return req


def grant_consent(
    db: Session,
    *,
    request_id: str,
    consent: bool,
    ip: str | None = None,
) -> SignRequest:
    if not consent:
        raise OperationError("Persetujuan wajib diberikan.", 400)
    req = db.get(SignRequest, request_id)
    if req is None:
        raise OperationError("Permintaan tidak ditemukan.", 404)
    if req.status not in ("pending", "consented"):
        raise OperationError("Status permintaan tidak valid.", 409)

    req.consent_at = utcnow()
    req.consent_ip = ip
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
    return req


def sign_request(
    db: Session,
    *,
    request_id: str,
    settings: Settings | None = None,
) -> SignResult:
    settings = settings or get_settings()
    req = db.get(SignRequest, request_id)
    if req is None:
        raise OperationError("Permintaan tidak ditemukan.", 404)
    if req.status != "consented":
        raise OperationError("Permintaan belum disetujui (consent).", 409)

    doc = req.document
    if doc is None:
        raise OperationError("Dokumen tidak ditemukan.", 404)
    if doc.status == "signed" and not settings.enable_multisign:
        raise OperationError(
            "Dokumen sudah ditandatangani. Multi-tanda tangan nonaktif.", 409
        )
    if doc.status not in ("uploaded", "signed"):
        raise OperationError("Status dokumen tidak valid.", 409)

    source_path = (
        Path(doc.signed_path)
        if doc.signed_path and Path(doc.signed_path).exists()
        else Path(doc.original_path)
    )
    if not source_path.exists():
        raise OperationError("Berkas sumber tidak ditemukan.", 404)

    signer = req.signer
    token = make_token(doc.id, settings.token_secret)
    verify_url = verify_url_for(doc.short_id, token)
    field_index = len(doc.signatures)

    letter = db.execute(
        select(LetterRequest)
        .where(LetterRequest.document_id == doc.id)
        .order_by(LetterRequest.submitted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    institution = letter.institution_name if letter else settings.institution
    faculty = letter.faculty_name if letter else None
    program = letter.program_name if letter else None

    try:
        signer_material = load_signer_for_profile(signer, settings)
    except PkiError as exc:
        raise OperationError(
            f"Kunci penanda tangan tidak dapat dimuat: {exc}", 500
        ) from exc

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
            institution=institution,
            faculty=faculty,
            program=program,
            multi_mode=settings.enable_multisign,
            signer_material=signer_material,
        )
    except SigningError as exc:
        raise OperationError(str(exc), 502) from exc
    except Exception as exc:  # noqa: BLE001
        raise OperationError(f"Gagal menandatangani: {exc}", 500) from exc

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
