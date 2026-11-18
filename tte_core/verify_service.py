from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    Document,
    SignatureRecord,
    SignRequest,
    SignerProfile,
    VerificationToken,
)
from .tokens import verify_token
from .verification import validate_pdf_bytes


class VerificationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def get_document_by_short_id(
    db: Session, short_id: str
) -> Document | None:
    return db.execute(
        select(Document).where(Document.short_id == short_id)
    ).scalar_one_or_none()


def signer_profiles_for_document(
    db: Session, document_id: str
) -> dict[str, dict]:
    rows = db.execute(
        select(SignatureRecord, SignerProfile)
        .join(
            SignRequest,
            SignatureRecord.sign_request_id == SignRequest.id,
        )
        .join(SignerProfile, SignRequest.signer_id == SignerProfile.id)
        .where(SignatureRecord.document_id == document_id)
    ).all()
    return {
        record.field_name: {
            "name": signer.name,
            "jabatan": signer.jabatan,
            "nip": signer.nip,
            "email": signer.email,
        }
        for record, signer in rows
    }


def enrich_signature_profiles(
    db: Session, document_id: str, result: dict
) -> dict:
    profiles = signer_profiles_for_document(db, document_id)
    for signature in result.get("signatures", []):
        profile = profiles.get(signature.get("field_name") or "")
        signature["signer_profile"] = profile
    return result


def verify_document(
    db: Session,
    settings: Settings,
    short_id: str,
    token: str,
) -> dict:
    doc = get_document_by_short_id(db, short_id)
    if doc is None:
        raise VerificationError("Dokumen tidak ditemukan.", 404)
    if not verify_token(doc.id, token, settings.token_secret):
        raise VerificationError("Token verifikasi tidak valid.", 403)

    record = db.execute(
        select(VerificationToken).where(
            VerificationToken.document_id == doc.id
        )
    ).scalar_one_or_none()
    if record is not None:
        record.hit_count += 1

    if not doc.signed_path or not Path(doc.signed_path).exists():
        return {
            "valid": False,
            "status": "UNSIGNED",
            "document": {
                "short_id": doc.short_id,
                "filename": doc.filename,
                "sha256": doc.source_hash,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
            },
            "signatures": [],
        }

    data = Path(doc.signed_path).read_bytes()
    result = validate_pdf_bytes(data, settings)
    result["document"] = {
        "id": doc.id,
        "short_id": doc.short_id,
        "filename": doc.filename,
        "sha256": doc.source_hash,
        "final_sha256": doc.final_hash,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "signed": True,
        "download_url": (
            f"{settings.public_base_url}/api/v1/documents/{doc.id}/download?t={token}"
        ),
        "verify_url": f"{settings.verify_base_url}/v/{doc.short_id}?t={token}",
    }
    enrich_signature_profiles(db, doc.id, result)
    return result
