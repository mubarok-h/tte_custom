from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..admin_auth import csrf_token, require_csrf
from ..audit import append_event
from ..config import get_settings
from ..db import get_db
from ..models import (
    Document,
    Faculty,
    Institution,
    LetterRequest,
    StudyProgram,
)
from ..tokens import make_token
from ..verification import validate_pdf_bytes
from ..verify_service import enrich_signature_profiles

router = APIRouter(tags=["letters"])
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES))

LETTER_TYPES = (
    "Surat Keterangan Aktif Kuliah",
    "Surat Pengantar Penelitian",
    "Surat Keterangan Lulus",
    "Surat Rekomendasi",
    "Surat Keterangan Lainnya",
)
LETTER_STATUS_LABELS = {
    "submitted": "Diajukan",
    "verified": "Diverifikasi",
    "approved": "Disetujui",
    "signed": "Terbit (TTE)",
    "rejected": "Ditolak",
}
ACADEMIC_YEARS = tuple(
    f"{year}/{year + 1} {semester}"
    for year in range(2023, 2028)
    for semester in ("Ganjil", "Genap")
)


def _fmt_dt(value) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().strftime("%d-%m-%Y %H:%M")
    return value.strftime("%d-%m-%Y")


templates.env.filters["tanggal"] = _fmt_dt
templates.env.globals["letter_status_label"] = lambda value: (
    LETTER_STATUS_LABELS.get(value or "", value or "-")
)
templates.env.globals["current_year"] = datetime.now().year


def _redirect(url: str, msg: str | None = None, kind: str = "ok") -> RedirectResponse:
    if msg:
        from urllib.parse import urlencode

        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'msg': msg, 'kind': kind})}"
    return RedirectResponse(url=url, status_code=303)


def _new_request_no(db: Session) -> str:
    for _ in range(8):
        candidate = (
            f"REQ-{datetime.now():%Y%m%d}-"
            f"{secrets.token_hex(3).upper()}"
        )
        exists = db.execute(
            select(LetterRequest.id)
            .where(LetterRequest.request_no == candidate)
            .limit(1)
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    return f"REQ-{datetime.now():%Y%m%d}-{secrets.token_hex(4).upper()}"


def _master_data(db: Session) -> list[dict]:
    institutions = (
        db.execute(
            select(Institution)
            .where(Institution.is_active.is_(True))
            .order_by(Institution.name.asc())
        )
        .scalars()
        .all()
    )
    faculties = (
        db.execute(
            select(Faculty)
            .where(Faculty.is_active.is_(True))
            .order_by(Faculty.name.asc())
        )
        .scalars()
        .all()
    )
    programs = (
        db.execute(
            select(StudyProgram)
            .where(StudyProgram.is_active.is_(True))
            .order_by(StudyProgram.name.asc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": inst.id,
            "name": inst.name,
            "faculties": [
                {
                    "id": fac.id,
                    "name": fac.name,
                    "programs": [
                        {"id": prog.id, "name": prog.name, "level": prog.level}
                        for prog in programs
                        if prog.faculty_id == fac.id
                    ],
                }
                for fac in faculties
                if fac.institution_id == inst.id
            ],
        }
        for inst in institutions
    ]


def _form_context(
    request: Request,
    db: Session,
    *,
    form: dict | None = None,
    error: str | None = None,
) -> dict:
    settings = get_settings()
    master = _master_data(db)
    return {
        "request": request,
        "institution": settings.institution,
        "csrf": csrf_token(settings),
        "master_data": master,
        "master_json": master,
        "letter_types": LETTER_TYPES,
        "academic_years": ACADEMIC_YEARS,
        "form": form or {},
        "error": error,
        "msg": request.query_params.get("msg"),
        "kind": request.query_params.get("kind", "ok"),
    }


@router.get("/permohonan", response_class=HTMLResponse)
def request_form(
    request: Request,
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "permohonan.html", _form_context(request, db)
    )


@router.post("/permohonan", response_class=HTMLResponse)
def request_submit(
    request: Request,
    applicant_name: str = Form(default=""),
    nim: str = Form(default=""),
    birth_place: str = Form(default=""),
    birth_date: str = Form(default=""),
    email: str = Form(default=""),
    phone: str = Form(default=""),
    address: str = Form(default=""),
    institution_id: str = Form(default=""),
    faculty_id: str = Form(default=""),
    program_id: str = Form(default=""),
    semester: str = Form(default=""),
    academic_year: str = Form(default=""),
    letter_type: str = Form(default=""),
    purpose: str = Form(default=""),
    notes: str = Form(default=""),
    research_title: str = Form(default=""),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    form = {
        "applicant_name": applicant_name,
        "nim": nim,
        "birth_place": birth_place,
        "birth_date": birth_date,
        "email": email,
        "phone": phone,
        "address": address,
        "institution_id": institution_id,
        "faculty_id": faculty_id,
        "program_id": program_id,
        "semester": semester,
        "academic_year": academic_year,
        "letter_type": letter_type,
        "purpose": purpose,
        "notes": notes,
        "research_title": research_title,
    }

    applicant_name = applicant_name.strip()
    nim = nim.strip()
    email = email.strip()
    if not applicant_name or not nim or not email:
        return templates.TemplateResponse(
            request,
            "permohonan.html",
            _form_context(
                request,
                db,
                form=form,
                error="Nama lengkap, NIM, dan email wajib diisi.",
            ),
            status_code=400,
        )
    if letter_type not in LETTER_TYPES:
        return templates.TemplateResponse(
            request,
            "permohonan.html",
            _form_context(
                request, db, form=form, error="Pilih jenis surat yang valid."
            ),
            status_code=400,
        )

    institution = db.get(Institution, institution_id) if institution_id else None
    faculty = db.get(Faculty, faculty_id) if faculty_id else None
    program = db.get(StudyProgram, program_id) if program_id else None
    if institution is None or faculty is None or program is None:
        return templates.TemplateResponse(
            request,
            "permohonan.html",
            _form_context(
                request,
                db,
                form=form,
                error="Lembaga, fakultas, dan program studi wajib dipilih.",
            ),
            status_code=400,
        )
    if (
        faculty.institution_id != institution.id
        or program.faculty_id != faculty.id
    ):
        return templates.TemplateResponse(
            request,
            "permohonan.html",
            _form_context(
                request,
                db,
                form=form,
                error="Kombinasi lembaga, fakultas, dan prodi tidak cocok.",
            ),
            status_code=400,
        )

    birth: date | None = None
    if birth_date:
        try:
            birth = date.fromisoformat(birth_date)
        except ValueError:
            birth = None

    letter = LetterRequest(
        request_no=_new_request_no(db),
        applicant_name=applicant_name,
        nim=nim,
        birth_place=birth_place.strip() or None,
        birth_date=birth,
        email=email,
        phone=phone.strip() or None,
        address=address.strip() or None,
        institution_id=institution.id,
        faculty_id=faculty.id,
        program_id=program.id,
        institution_name=institution.name,
        faculty_name=faculty.name,
        program_name=program.name,
        semester=semester.strip() or None,
        academic_year=academic_year.strip() or None,
        letter_type=letter_type,
        purpose=purpose.strip() or None,
        notes=notes.strip() or None,
        research_title=research_title.strip() or None,
        status="submitted",
    )
    db.add(letter)
    db.flush()
    append_event(
        db,
        "letter.submitted",
        {
            "letter_id": letter.id,
            "request_no": letter.request_no,
            "nim": letter.nim,
            "letter_type": letter.letter_type,
        },
    )
    db.commit()
    return _redirect(
        f"/permohonan/status?q={letter.request_no}",
        f"Permohonan {letter.request_no} berhasil dikirim.",
    )


@router.get("/permohonan/status", response_class=HTMLResponse)
def request_status(
    request: Request,
    q: str = Query(default=""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    query_text = q.strip()
    results = []
    if query_text:
        results = (
            db.execute(
                select(LetterRequest)
                .where(
                    or_(
                        LetterRequest.nim == query_text,
                        LetterRequest.request_no == query_text.upper(),
                    )
                )
                .order_by(LetterRequest.submitted_at.desc())
                .limit(25)
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        request,
        "permohonan_status.html",
        {
            "request": request,
            "institution": settings.institution,
            "q": query_text,
            "letters": results,
            "msg": request.query_params.get("msg"),
            "kind": request.query_params.get("kind", "ok"),
        },
    )


def _validation_context(
    request: Request,
    db: Session,
    no: str,
    *,
    doc_result: dict | None = None,
    doc_error: str | None = None,
    file_name: str | None = None,
) -> dict:
    settings = get_settings()
    query_text = no.strip()
    letter = None
    if query_text:
        letter = db.execute(
            select(LetterRequest)
            .where(
                or_(
                    LetterRequest.letter_no == query_text,
                    LetterRequest.request_no == query_text.upper(),
                )
            )
            .limit(1)
        ).scalar_one_or_none()

    verify_url = None
    if letter and letter.document_id and letter.status == "signed":
        from ..models import Document

        document = db.get(Document, letter.document_id)
        if document is not None:
            token = make_token(document.id, settings.token_secret)
            verify_url = (
                f"{settings.verify_base_url}/v/{document.short_id}?t={token}"
            )

    return {
        "request": request,
        "institution": settings.institution,
        "csrf": csrf_token(settings),
        "no": query_text,
        "letter": letter,
        "valid": bool(letter and letter.status in ("approved", "signed")),
        "verify_url": verify_url,
        "doc_result": doc_result,
        "doc_error": doc_error,
        "file_name": file_name,
    }


@router.get("/permohonan/validasi", response_class=HTMLResponse)
def request_validate(
    request: Request,
    no: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "permohonan_validasi.html", _validation_context(request, db, no)
    )


@router.post("/permohonan/validasi/dokumen", response_class=HTMLResponse)
async def validate_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    settings = get_settings()
    data = await file.read()
    doc_error = None
    doc_result = None

    if not data:
        doc_error = "File kosong."
    elif len(data) > settings.max_upload_bytes:
        doc_error = "Ukuran file melebihi batas."
    elif not data.startswith(b"%PDF"):
        doc_error = "File bukan PDF."
    else:
        doc_result = await run_in_threadpool(
            validate_pdf_bytes, data, settings
        )
        # Cocokkan dokumen yang diunggah dengan arsip agar profil penanda
        # tangan asli (bukan hanya subjek sertifikat) dapat ditampilkan.
        from ..tokens import sha256_hex

        digest = sha256_hex(data)
        stored = db.execute(
            select(Document)
            .where(
                (Document.final_hash == digest)
                | (Document.source_hash == digest)
            )
            .limit(1)
        ).scalar_one_or_none()
        if stored is not None:
            enrich_signature_profiles(db, stored.id, doc_result)

    return templates.TemplateResponse(
        request,
        "permohonan_validasi.html",
        _validation_context(
            request,
            db,
            "",
            doc_result=doc_result,
            doc_error=doc_error,
            file_name=file.filename,
        ),
    )
