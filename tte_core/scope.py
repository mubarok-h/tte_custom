from __future__ import annotations

"""Pembatasan data berdasarkan unit pengguna (fakultas/prodi).

- Administrator dan Operator TU melihat seluruh data.
- Operator Fakultas hanya melihat data prodi di fakultasnya.
- Operator Prodi hanya melihat data prodinya sendiri.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .admin_auth import AdminIdentity
from .models import Document, LetterRequest

GLOBAL_ROLES = {"admin", "operator"}


def is_global(user: AdminIdentity | None) -> bool:
    if user is None:
        return False
    # Hanya administrator dan operator TU yang melihat seluruh data.
    return user.role in GLOBAL_ROLES


def letter_condition(user: AdminIdentity):
    """Kondisi SQL untuk membatasi LetterRequest, atau None bila global.

    Role unit yang belum diisi fakultas/prodi tidak melihat data apa pun
    (fail-closed), bukan malah melihat semuanya.
    """
    if user is None:
        return LetterRequest.id.is_(None)
    if is_global(user):
        return None
    if user.role == "operator_fakultas" and user.faculty_id:
        return LetterRequest.faculty_id == user.faculty_id
    if user.role == "operator_prodi" and user.program_id:
        return LetterRequest.program_id == user.program_id
    return LetterRequest.id.is_(None)


def scoped_letter_ids(user: AdminIdentity):
    """Subquery berisi dokumen milik permohonan dalam lingkup user."""
    condition = letter_condition(user)
    query = select(LetterRequest.document_id).where(
        LetterRequest.document_id.is_not(None)
    )
    if condition is not None:
        query = query.where(condition)
    return query


def document_condition(user: AdminIdentity):
    if is_global(user):
        return None
    return Document.id.in_(scoped_letter_ids(user))


def request_condition(user: AdminIdentity, model):
    """Untuk SignRequest: batasi lewat dokumen milik permohonan."""
    if is_global(user):
        return None
    return model.document_id.in_(scoped_letter_ids(user))


def can_access_letter(user: AdminIdentity, letter: LetterRequest) -> bool:
    if is_global(user):
        return True
    if user.role == "operator_fakultas" and user.faculty_id:
        return letter.faculty_id == user.faculty_id
    if user.role == "operator_prodi" and user.program_id:
        return letter.program_id == user.program_id
    return False


def letter_for_document(db: Session, document_id: str) -> LetterRequest | None:
    return db.execute(
        select(LetterRequest)
        .where(LetterRequest.document_id == document_id)
        .order_by(LetterRequest.submitted_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def can_access_document(
    db: Session, user: AdminIdentity, document: Document
) -> bool:
    if is_global(user):
        return True
    letter = letter_for_document(db, document.id)
    if letter is None:
        return False
    return can_access_letter(user, letter)
