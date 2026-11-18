from __future__ import annotations

import hmac
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..admin_auth import (
    ADMIN_COOKIE,
    create_session,
    csrf_token,
    is_authenticated,
    require_admin,
    require_csrf,
    require_superuser,
    weak_credentials,
)
from ..audit import append_event, verify_chain
from ..config import get_settings
from ..db import get_db
from ..models import (
    AuditEvent,
    Document,
    Faculty,
    Institution,
    LetterRequest,
    SignatureRecord,
    SignerProfile,
    SignRequest,
    StudyProgram,
    User,
    VerificationToken,
    utcnow,
)
from ..pki import PkiError
from ..signer_certs import (
    delete_signer_certificate,
    issue_signer_certificate,
)
from ..operations import (
    OperationError,
    create_sign_request as create_sign_request_op,
    grant_consent as grant_consent_op,
    sign_request as sign_request_op,
    upload_document as upload_document_op,
)
from ..security import hash_password, verify_password
from ..storage import original_path
from ..tokens import make_token
from ..verify_service import VerificationError, verify_document

router = APIRouter(prefix="/admin", tags=["admin"])
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES))

PAGE_SIZE = 20
DOC_STATUSES = ("uploaded", "signed", "archived")
REQUEST_STATUSES = ("pending", "consented", "signed", "cancelled")

_FAILED_LOGINS: dict[str, list[float]] = {}
_MAX_FAILURES = 8
_FAILURE_WINDOW = 600.0

_DOWNLOAD_VARIANTS = {"original", "signed"}

USER_ROLES = ("admin", "operator")
LETTER_STATUSES = ("submitted", "verified", "approved", "signed", "rejected")
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


def _human_size(value: int | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _fmt_dt(value) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().strftime("%d-%m-%Y %H:%M")
    return str(value)


def _pretty_json(value) -> str:
    import json

    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["filesize"] = _human_size
templates.env.filters["tanggal"] = _fmt_dt
templates.env.filters["prettyjson"] = _pretty_json


_BADGE_STYLES = {
    "signed": "success",
    "valid": "success",
    "active": "success",
    "ok": "success",
    "pending": "warning",
    "warn": "warning",
    "consented": "primary",
    "uploaded": "info",
    "inactive": "secondary",
    "cancelled": "secondary",
    "unsigned": "secondary",
    "archived": "secondary",
    "invalid": "danger",
    "error": "danger",
    "not_found": "danger",
    "revoked": "danger",
    "submitted": "primary",
    "verified": "warning",
    "approved": "success",
    "rejected": "danger",
}


def _badge_class(status: str | None) -> str:
    return _BADGE_STYLES.get((status or "").lower(), "secondary")


def _letter_status_label(status: str | None) -> str:
    return LETTER_STATUS_LABELS.get(status or "", status or "-")


templates.env.globals["badge_class"] = _badge_class
templates.env.globals["letter_status_label"] = _letter_status_label
templates.env.globals["current_year"] = datetime.now().year


def _context(request: Request, *, active: str, **extra) -> dict:
    settings = get_settings()
    context = {
        "request": request,
        "institution": settings.institution,
        "active": active,
        "csrf": csrf_token(settings),
        "msg": request.query_params.get("msg"),
        "kind": (
            request.query_params.get("kind")
            if request.query_params.get("kind") in {"ok", "err", "warn"}
            else "ok"
        ),
        "warnings": weak_credentials(settings),
        "settings": settings,
        "admin_username": settings.admin_username,
        "current_user": getattr(request.state, "user", None),
    }
    context.update(extra)
    return context


def _redirect(
    url: str, msg: str | None = None, kind: str = "ok"
) -> RedirectResponse:
    if msg:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'msg': msg, 'kind': kind})}"
    return RedirectResponse(url=url, status_code=303)


def _safe_next(value: str | None, fallback: str = "/admin") -> str:
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and not any(ch in value for ch in ("\r", "\n", "\x00"))
    ):
        return value
    return fallback


def _page_url(request: Request, page: int) -> str:
    params = dict(request.query_params)
    params["page"] = str(page)
    params.pop("msg", None)
    params.pop("kind", None)
    return f"{request.url.path}?{urlencode(params)}"


def _paginate(request: Request, *, page: int, total: int) -> dict:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "prev_url": _page_url(request, page - 1) if page > 1 else None,
        "next_url": _page_url(request, page + 1) if page < pages else None,
    }


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _login_blocked(ip: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _FAILED_LOGINS.get(ip, []) if now - t < _FAILURE_WINDOW]
    _FAILED_LOGINS[ip] = attempts
    return len(attempts) >= _MAX_FAILURES


def _record_login_failure(ip: str) -> None:
    _FAILED_LOGINS.setdefault(ip, []).append(time.monotonic())


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = Query(default="/admin")):
    if is_authenticated(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {
            "institution": settings.institution,
            "csrf": csrf_token(settings),
            "next": _safe_next(next),
            "error": None,
            "username": settings.admin_username,
            "warnings": weak_credentials(settings),
            "auth_page": True,
            "settings": settings,
        },
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default="/admin"),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    settings = get_settings()
    ip = _client_ip(request)
    target = _safe_next(next)

    def failed(message: str, status_code: int = 401):
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {
                "institution": settings.institution,
                "csrf": csrf_token(settings),
                "next": target,
                "error": message,
                "username": username or settings.admin_username,
                "warnings": weak_credentials(settings),
                "auth_page": True,
            },
            status_code=status_code,
        )

    if _login_blocked(ip):
        return failed(
            "Terlalu banyak percobaan gagal. Coba lagi beberapa menit lagi.",
            status_code=429,
        )

    user = db.execute(
        select(User).where(User.username == username.strip().lower())
    ).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(password, user.password_hash)
    ):
        _record_login_failure(ip)
        return failed("Username atau password salah.")

    _FAILED_LOGINS.pop(ip, None)
    user.last_login_at = utcnow()
    append_event(
        db,
        "user.login",
        {"user_id": user.id, "username": user.username},
    )
    db.commit()
    response = RedirectResponse(target, status_code=303)
    secure = settings.public_base_url.startswith(
        "https://"
    ) or settings.verify_base_url.startswith("https://")
    response.set_cookie(
        ADMIN_COOKIE,
        create_session(settings, user),
        max_age=int(settings.admin_session_hours * 3600),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return response


@router.post("/logout")
def logout(_admin: str = Depends(require_admin), _csrf: None = Depends(require_csrf)):
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    settings = get_settings()
    document_total = db.scalar(select(func.count()).select_from(Document)) or 0
    signed_total = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.status == "signed")
        )
        or 0
    )
    unsigned_total = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.status == "uploaded")
        )
        or 0
    )
    signer_total = db.scalar(select(func.count()).select_from(SignerProfile)) or 0
    active_signer_total = (
        db.scalar(
            select(func.count())
            .select_from(SignerProfile)
            .where(SignerProfile.status == "active")
        )
        or 0
    )
    active_requests = (
        db.scalar(
            select(func.count())
            .select_from(SignRequest)
            .where(SignRequest.status.in_(("pending", "consented")))
        )
        or 0
    )
    audit_total = db.scalar(select(func.count()).select_from(AuditEvent)) or 0

    letter_pending = (
        db.scalar(
            select(func.count())
            .select_from(LetterRequest)
            .where(LetterRequest.status.in_(("submitted", "verified")))
        )
        or 0
    )
    letter_approved = (
        db.scalar(
            select(func.count())
            .select_from(LetterRequest)
            .where(LetterRequest.status.in_(("approved", "signed")))
        )
        or 0
    )
    recent_letters = (
        db.execute(
            select(LetterRequest)
            .order_by(LetterRequest.submitted_at.desc())
            .limit(6)
        )
        .scalars()
        .all()
    )

    chain_ok, chain_count = verify_chain(db)
    recent_documents = (
        db.execute(
            select(Document).order_by(Document.created_at.desc()).limit(8)
        )
        .scalars()
        .all()
    )
    recent_requests = (
        db.execute(
            select(SignRequest).order_by(SignRequest.created_at.desc()).limit(8)
        )
        .scalars()
        .all()
    )
    recent_events = (
        db.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(8))
        .scalars()
        .all()
    )

    signing_key = "Belum dikonfigurasi"
    signing_key_ok = False
    if settings.pkcs11_lib:
        signing_key = "PKCS#11 / HSM"
        signing_key_ok = True
    elif settings.signing_p12:
        signing_key = f"File P12 · {settings.signing_p12.name}"
        signing_key_ok = settings.signing_p12.exists()

    crl_path = settings.crl_path
    crl_exists = crl_path.exists()
    crl_mtime = None
    if crl_exists:
        try:
            crl_mtime = datetime.fromtimestamp(crl_path.stat().st_mtime, timezone.utc)
        except OSError:
            crl_mtime = None

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        _context(
            request,
            active="dashboard",
            document_total=document_total,
            signed_total=signed_total,
            unsigned_total=unsigned_total,
            signer_total=signer_total,
            active_signer_total=active_signer_total,
            active_requests=active_requests,
            audit_total=audit_total,
            letter_pending=letter_pending,
            letter_approved=letter_approved,
            recent_letters=recent_letters,
            chain_ok=chain_ok,
            chain_count=chain_count,
            recent_documents=recent_documents,
            recent_requests=recent_requests,
            recent_events=recent_events,
            signing_key=signing_key,
            signing_key_ok=signing_key_ok,
            crl_exists=crl_exists,
            crl_mtime=crl_mtime,
            settings=settings,
        ),
    )


@router.get("/documents", response_class=HTMLResponse)
def documents_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    conditions = []
    query_text = q.strip()
    if query_text:
        like = f"%{query_text}%"
        conditions.append(
            or_(Document.filename.ilike(like), Document.short_id.ilike(like))
        )
    if status in DOC_STATUSES:
        conditions.append(Document.status == status)

    total = (
        db.scalar(
            select(func.count()).select_from(Document).where(*conditions)
        )
        or 0
    )
    pagination = _paginate(request, page=page, total=total)
    rows = (
        db.execute(
            select(Document)
            .where(*conditions)
            .order_by(Document.created_at.desc())
            .offset((pagination["page"] - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/documents.html",
        _context(
            request,
            active="documents",
            documents=rows,
            q=query_text,
            status=status,
            statuses=DOC_STATUSES,
            pagination=pagination,
        ),
    )


@router.post("/documents")
async def documents_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    data = await file.read()
    try:
        doc = upload_document_op(
            db,
            data=data,
            filename=file.filename or "dokumen.pdf",
        )
    except OperationError as exc:
        return _redirect("/admin/documents", exc.message, "err")
    return _redirect(
        f"/admin/documents/{doc.id}",
        f"Dokumen {doc.short_id} berhasil diunggah.",
    )


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(
    request: Request,
    document_id: str,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    signers = (
        db.execute(
            select(SignerProfile)
            .where(SignerProfile.status == "active")
            .order_by(SignerProfile.name.asc())
        )
        .scalars()
        .all()
    )
    requests = (
        db.execute(
            select(SignRequest)
            .where(SignRequest.document_id == doc.id)
            .order_by(SignRequest.created_at.desc())
        )
        .scalars()
        .all()
    )
    signatures = (
        db.execute(
            select(SignatureRecord)
            .where(SignatureRecord.document_id == doc.id)
            .order_by(SignatureRecord.signed_at.asc())
        )
        .scalars()
        .all()
    )
    token = make_token(doc.id, get_settings().token_secret)
    settings = get_settings()
    verify_link = f"{settings.verify_base_url}/v/{doc.short_id}?t={token}"
    return templates.TemplateResponse(
        request,
        "admin/document_detail.html",
        _context(
            request,
            active="documents",
            doc=doc,
            signers=signers,
            requests=requests,
            signatures=signatures,
            verify_link=verify_link,
            signed_exists=bool(doc.signed_path and Path(doc.signed_path).exists()),
            current_path=f"/admin/documents/{doc.id}",
        ),
    )


@router.post("/documents/{document_id}/delete")
def document_delete(
    request: Request,
    document_id: str,
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, "/admin/documents")
    doc = db.get(Document, document_id)
    if doc is None:
        return _redirect(target, "Dokumen tidak ditemukan.", "err")

    linked_letter = db.execute(
        select(LetterRequest).where(LetterRequest.document_id == doc.id).limit(1)
    ).scalar_one_or_none()
    if linked_letter is not None:
        return _redirect(
            target,
            "Dokumen terhubung ke permohonan surat dan tidak dapat dihapus.",
            "err",
        )

    settings = get_settings()
    metadata = {
        "document_id": doc.id,
        "short_id": doc.short_id,
        "filename": doc.filename,
        "source_hash": doc.source_hash,
        "final_hash": doc.final_hash,
        "status": doc.status,
    }
    append_event(db, "document.deleted", metadata)

    doc_id = doc.id
    tokens = (
        db.execute(
            select(VerificationToken).where(
                VerificationToken.document_id == doc_id
            )
        )
        .scalars()
        .all()
    )
    for token in tokens:
        db.delete(token)

    db.delete(doc)
    db.commit()

    shutil.rmtree(settings.documents_dir / doc_id, ignore_errors=True)
    return _redirect(target, f"Dokumen {metadata['short_id']} dihapus.")


@router.get("/documents/{document_id}/file")
def document_file(
    document_id: str,
    variant: str = Query(default="signed"),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    if variant not in _DOWNLOAD_VARIANTS:
        raise HTTPException(status_code=400, detail="Varian berkas tidak dikenal.")

    settings = get_settings()
    if variant == "signed":
        if not doc.signed_path:
            raise HTTPException(status_code=404, detail="PDF bertanda tangan belum ada.")
        path = Path(doc.signed_path)
        filename = f"{Path(doc.filename).stem}-tte.pdf"
    else:
        path = Path(doc.original_path) if doc.original_path else original_path(
            settings, doc.id
        )
        filename = Path(doc.filename).name

    if not path.exists():
        raise HTTPException(status_code=404, detail="Berkas tidak tersedia.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
    )


@router.post("/documents/{document_id}/requests")
def document_create_request(
    request: Request,
    document_id: str,
    signer_id: str = Form(default=""),
    reason: str = Form(default="Pengesahan Dokumen Resmi"),
    location: str = Form(default=""),
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/documents/{document_id}")
    if not signer_id:
        return _redirect(target, "Pilih penanda tangan terlebih dahulu.", "err")

    existing = (
        db.execute(
            select(SignRequest).where(
                SignRequest.document_id == document_id,
                SignRequest.signer_id == signer_id,
                SignRequest.status.in_(("pending", "consented")),
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return _redirect(
            target,
            "Masih ada permintaan aktif untuk penanda tangan tersebut.",
            "err",
        )

    try:
        create_sign_request_op(
            db,
            document_id=document_id,
            signer_id=signer_id,
            reason=reason.strip() or "Pengesahan Dokumen Resmi",
            location=location.strip() or None,
        )
    except OperationError as exc:
        return _redirect(target, exc.message, "err")
    return _redirect(target, "Permintaan tanda tangan dibuat.")


@router.get("/signers", response_class=HTMLResponse)
def signers_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    conditions = []
    query_text = q.strip()
    if query_text:
        like = f"%{query_text}%"
        conditions.append(
            or_(
                SignerProfile.name.ilike(like),
                SignerProfile.nip.ilike(like),
                SignerProfile.jabatan.ilike(like),
            )
        )
    if status in ("active", "inactive"):
        conditions.append(SignerProfile.status == status)

    total = (
        db.scalar(
            select(func.count()).select_from(SignerProfile).where(*conditions)
        )
        or 0
    )
    pagination = _paginate(request, page=page, total=total)
    signers = (
        db.execute(
            select(SignerProfile)
            .where(*conditions)
            .order_by(SignerProfile.created_at.desc())
            .offset((pagination["page"] - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/signers.html",
        _context(
            request,
            active="signers",
            signers=signers,
            q=query_text,
            status=status,
            pagination=pagination,
        ),
    )


@router.post("/signers")
def signers_create(
    request: Request,
    name: str = Form(default=""),
    jabatan: str = Form(default=""),
    nip: str = Form(default=""),
    email: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = "/admin/signers"
    name, jabatan, nip, email = (
        name.strip(),
        jabatan.strip(),
        nip.strip(),
        email.strip(),
    )
    if not (name and jabatan and nip):
        return _redirect(target, "Nama, jabatan, dan NIP wajib diisi.", "err")

    existing = db.execute(
        select(SignerProfile).where(SignerProfile.nip == nip).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return _redirect(target, f"NIP {nip} sudah terdaftar.", "err")

    signer = SignerProfile(
        name=name,
        jabatan=jabatan,
        nip=nip,
        email=email or None,
        status="active",
    )
    db.add(signer)
    db.flush()

    settings = get_settings()
    cert_error = None
    try:
        issue_signer_certificate(signer, settings)
    except PkiError as exc:
        cert_error = str(exc)

    append_event(
        db,
        "signer.created",
        {
            "signer_id": signer.id,
            "name": signer.name,
            "nip": signer.nip,
            "jabatan": signer.jabatan,
            "cert_serial": signer.cert_serial,
        },
    )
    db.commit()
    if cert_error:
        return _redirect(
            target,
            f"Penanda tangan {signer.name} ditambahkan, tetapi sertifikat "
            f"gagal diterbitkan: {cert_error}",
            "err",
        )
    return _redirect(
        target,
        f"Penanda tangan {signer.name} ditambahkan dan sertifikat diterbitkan.",
    )


@router.post("/signers/{signer_id}/edit")
def signers_edit(
    request: Request,
    signer_id: str,
    name: str = Form(default=""),
    jabatan: str = Form(default=""),
    nip: str = Form(default=""),
    email: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    signer = db.get(SignerProfile, signer_id)
    if signer is None:
        raise HTTPException(status_code=404, detail="Penanda tangan tidak ditemukan.")
    name, jabatan, nip, email = (
        name.strip(),
        jabatan.strip(),
        nip.strip(),
        email.strip(),
    )
    if not (name and jabatan and nip):
        return _redirect("/admin/signers", "Nama, jabatan, dan NIP wajib diisi.", "err")
    duplicate = db.execute(
        select(SignerProfile)
        .where(SignerProfile.nip == nip, SignerProfile.id != signer.id)
        .limit(1)
    ).scalar_one_or_none()
    if duplicate is not None:
        return _redirect("/admin/signers", f"NIP {nip} sudah terdaftar.", "err")
    signer.name = name
    signer.jabatan = jabatan
    signer.nip = nip
    signer.email = email or None
    append_event(
        db,
        "signer.updated",
        {"signer_id": signer.id, "nip": signer.nip},
    )
    db.commit()
    return _redirect("/admin/signers", f"Data {signer.name} diperbarui.")


@router.post("/signers/{signer_id}/certificate")
def signers_issue_certificate(
    request: Request,
    signer_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    signer = db.get(SignerProfile, signer_id)
    if signer is None:
        raise HTTPException(status_code=404, detail="Penanda tangan tidak ditemukan.")
    settings = get_settings()
    try:
        issue_signer_certificate(signer, settings)
    except PkiError as exc:
        return _redirect("/admin/signers", exc.message, "err")
    append_event(
        db,
        "signer.certificate_issued",
        {
            "signer_id": signer.id,
            "cert_serial": signer.cert_serial,
        },
    )
    db.commit()
    return _redirect(
        "/admin/signers", f"Sertifikat {signer.name} berhasil diterbitkan."
    )


@router.post("/signers/{signer_id}/delete")
def signers_delete(
    request: Request,
    signer_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    signer = db.get(SignerProfile, signer_id)
    if signer is None:
        raise HTTPException(status_code=404, detail="Penanda tangan tidak ditemukan.")
    used = (
        db.scalar(
            select(func.count())
            .select_from(SignRequest)
            .where(SignRequest.signer_id == signer.id)
        )
        or 0
    )
    if used:
        return _redirect(
            "/admin/signers",
            "Penanda tangan masih dipakai pada permintaan tanda tangan.",
            "err",
        )
    settings = get_settings()
    signer_id = signer.id
    signer_name = signer.name
    append_event(
        db,
        "signer.deleted",
        {"signer_id": signer.id, "nip": signer.nip, "name": signer.name},
    )
    db.delete(signer)
    db.commit()
    delete_signer_certificate(signer, settings)
    return _redirect("/admin/signers", f"Penanda tangan {signer_name} dihapus.")


@router.post("/signers/{signer_id}/status")
def signers_toggle_status(
    request: Request,
    signer_id: str,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    signer = db.get(SignerProfile, signer_id)
    if signer is None:
        raise HTTPException(status_code=404, detail="Penanda tangan tidak ditemukan.")
    signer.status = "inactive" if signer.status == "active" else "active"
    append_event(
        db,
        "signer.status_changed",
        {"signer_id": signer.id, "status": signer.status},
    )
    db.commit()
    label = "diaktifkan" if signer.status == "active" else "dinonaktifkan"
    return _redirect("/admin/signers", f"{signer.name} {label}.")


@router.get("/requests", response_class=HTMLResponse)
def requests_page(
    request: Request,
    status: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    conditions = []
    if status in REQUEST_STATUSES:
        conditions.append(SignRequest.status == status)

    total = (
        db.scalar(
            select(func.count()).select_from(SignRequest).where(*conditions)
        )
        or 0
    )
    pagination = _paginate(request, page=page, total=total)
    requests = (
        db.execute(
            select(SignRequest)
            .where(*conditions)
            .order_by(SignRequest.created_at.desc())
            .offset((pagination["page"] - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/requests.html",
        _context(
            request,
            active="requests",
            requests=requests,
            status=status,
            statuses=REQUEST_STATUSES,
            pagination=pagination,
        ),
    )


@router.post("/requests/{request_id}/consent")
def requests_consent(
    request: Request,
    request_id: str,
    next: str = Form(default="/admin/requests"),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, "/admin/requests")
    try:
        grant_consent_op(
            db,
            request_id=request_id,
            consent=True,
            ip=_client_ip(request),
        )
    except OperationError as exc:
        return _redirect(target, exc.message, "err")
    return _redirect(target, "Persetujuan (consent) dicatat.")


@router.post("/requests/{request_id}/sign")
def requests_sign(
    request: Request,
    request_id: str,
    next: str = Form(default="/admin/requests"),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, "/admin/requests")
    try:
        result = sign_request_op(db, request_id=request_id)
    except OperationError as exc:
        return _redirect(target, exc.message, "err")

    letter = db.execute(
        select(LetterRequest)
        .where(
            LetterRequest.document_id == result.document_id,
            LetterRequest.status == "approved",
        )
        .limit(1)
    ).scalar_one_or_none()
    if letter is not None:
        letter.status = "signed"
        letter.approved_at = letter.approved_at or utcnow()
        append_event(
            db,
            "letter.signed",
            {
                "letter_id": letter.id,
                "request_no": letter.request_no,
                "document_id": result.document_id,
                "pades_level": result.pades_level,
            },
        )
        db.commit()

    return _redirect(
        f"/admin/documents/{result.document_id}",
        f"Dokumen ditandatangani ({result.pades_level}). Unduh PDF di panel kanan.",
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    event_type: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    conditions = []
    if event_type:
        conditions.append(AuditEvent.event_type == event_type)

    total = (
        db.scalar(
            select(func.count()).select_from(AuditEvent).where(*conditions)
        )
        or 0
    )
    pagination = _paginate(request, page=page, total=total)
    events = (
        db.execute(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.id.desc())
            .offset((pagination["page"] - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        .scalars()
        .all()
    )
    event_types = (
        db.execute(
            select(AuditEvent.event_type)
            .distinct()
            .order_by(AuditEvent.event_type.asc())
        )
        .scalars()
        .all()
    )
    chain_ok, chain_count = verify_chain(db)
    return templates.TemplateResponse(
        request,
        "admin/audit.html",
        _context(
            request,
            active="audit",
            events=events,
            event_type=event_type,
            event_types=event_types,
            chain_ok=chain_ok,
            chain_count=chain_count,
            pagination=pagination,
        ),
    )


@router.get("/verify", response_class=HTMLResponse)
def verify_uploaded_page(
    request: Request,
    short_id: str = Query(default=""),
    t: str = Query(default=""),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin),
):
    """Pemeriksaan cepat dari panel admin memakai short_id + token."""
    result = None
    error = None
    status = None
    document = None
    if short_id and t:
        try:
            result = verify_document(db, get_settings(), short_id, t)
            db.commit()
            status = result.get("status")
            document = result.get("document")
        except VerificationError as exc:
            db.rollback()
            error = exc.message
            status = "NOT_FOUND" if exc.status_code == 404 else "ERROR"
    return templates.TemplateResponse(
        request,
        "admin/verify.html",
        _context(
            request,
            active="documents",
            result=result,
            document=document,
            error=error,
            status=status,
            short_id=short_id,
            token=t,
        ),
    )


# ---------------------------------------------------------------------------
# Manajemen pengguna (multi user)
# ---------------------------------------------------------------------------


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    q: str = Query(default=""),
    role: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
):
    conditions = []
    query_text = q.strip()
    if query_text:
        like = f"%{query_text}%"
        conditions.append(
            or_(
                User.name.ilike(like),
                User.username.ilike(like),
                User.email.ilike(like),
            )
        )
    if role in USER_ROLES:
        conditions.append(User.role == role)

    total = (
        db.scalar(select(func.count()).select_from(User).where(*conditions))
        or 0
    )
    pagination = _paginate(request, page=page, total=total)
    users = (
        db.execute(
            select(User)
            .where(*conditions)
            .order_by(User.created_at.desc())
            .offset((pagination["page"] - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        _context(
            request,
            active="users",
            users=users,
            q=query_text,
            role=role,
            roles=USER_ROLES,
            pagination=pagination,
        ),
    )


@router.post("/users")
def users_create(
    request: Request,
    name: str = Form(default=""),
    username: str = Form(default=""),
    email: str = Form(default=""),
    role: str = Form(default="operator"),
    password: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    target = "/admin/users"
    name = name.strip()
    username = username.strip().lower()
    email = email.strip()
    if not name or not username or not password:
        return _redirect(target, "Nama, username, dan password wajib diisi.", "err")
    if len(username) < 3:
        return _redirect(target, "Username minimal 3 karakter.", "err")
    if len(password) < 8:
        return _redirect(target, "Password minimal 8 karakter.", "err")
    if role not in USER_ROLES:
        return _redirect(target, "Role pengguna tidak dikenal.", "err")
    exists = db.execute(
        select(User).where(User.username == username).limit(1)
    ).scalar_one_or_none()
    if exists is not None:
        return _redirect(target, f"Username {username} sudah dipakai.", "err")

    user = User(
        name=name,
        username=username,
        email=email or None,
        role=role,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.flush()
    append_event(
        db,
        "user.created",
        {
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
        },
    )
    db.commit()
    return _redirect(target, f"Pengguna {user.username} ditambahkan.")


@router.post("/users/{user_id}/edit")
def users_edit(
    request: Request,
    user_id: str,
    name: str = Form(default=""),
    username: str = Form(default=""),
    email: str = Form(default=""),
    role: str = Form(default="operator"),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    name = name.strip()
    username = username.strip().lower()
    email = email.strip()
    if not name or not username:
        return _redirect("/admin/users", "Nama dan username wajib diisi.", "err")
    if len(username) < 3:
        return _redirect("/admin/users", "Username minimal 3 karakter.", "err")
    if role not in USER_ROLES:
        return _redirect("/admin/users", "Role pengguna tidak dikenal.", "err")
    if user.id == admin.id and role != "admin":
        return _redirect(
            "/admin/users", "Tidak dapat menurunkan role akun sendiri.", "err"
        )
    duplicate = db.execute(
        select(User)
        .where(User.username == username, User.id != user.id)
        .limit(1)
    ).scalar_one_or_none()
    if duplicate is not None:
        return _redirect("/admin/users", f"Username {username} sudah dipakai.", "err")

    user.name = name
    user.username = username
    user.email = email or None
    user.role = role
    append_event(
        db,
        "user.updated",
        {"user_id": user.id, "username": user.username, "role": user.role},
    )
    db.commit()
    return _redirect("/admin/users", f"Data {user.username} diperbarui.")


@router.post("/users/{user_id}/delete")
def users_delete(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    if user.id == admin.id:
        return _redirect("/admin/users", "Tidak dapat menghapus akun sendiri.", "err")
    if user.role == "admin" and user.is_active:
        active_admins = (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "admin", User.is_active.is_(True))
            )
            or 0
        )
        if active_admins <= 1:
            return _redirect(
                "/admin/users",
                "Tidak dapat menghapus administrator aktif terakhir.",
                "err",
            )
    append_event(
        db,
        "user.deleted",
        {"user_id": user.id, "username": user.username},
    )
    db.delete(user)
    db.commit()
    return _redirect("/admin/users", f"Pengguna {user.username} dihapus.")


@router.post("/users/{user_id}/status")
def users_toggle_status(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    if user.id == admin.id:
        return _redirect(
            "/admin/users", "Tidak dapat menonaktifkan akun sendiri.", "err"
        )
    user.is_active = not user.is_active
    append_event(
        db,
        "user.status_changed",
        {"user_id": user.id, "is_active": user.is_active},
    )
    db.commit()
    label = "diaktifkan" if user.is_active else "dinonaktifkan"
    return _redirect("/admin/users", f"{user.username} {label}.")


@router.post("/users/{user_id}/password")
def users_reset_password(
    request: Request,
    user_id: str,
    password: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    if len(password) < 8:
        return _redirect("/admin/users", "Password minimal 8 karakter.", "err")
    user.password_hash = hash_password(password)
    append_event(
        db, "user.password_reset", {"user_id": user.id, "username": user.username}
    )
    db.commit()
    return _redirect("/admin/users", f"Password {user.username} diperbarui.")


@router.post("/users/{user_id}/role")
def users_change_role(
    request: Request,
    user_id: str,
    role: str = Form(default="operator"),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    if role not in USER_ROLES:
        return _redirect("/admin/users", "Role pengguna tidak dikenal.", "err")
    if user.id == admin.id and role != "admin":
        return _redirect(
            "/admin/users", "Tidak dapat menurunkan role akun sendiri.", "err"
        )
    user.role = role
    append_event(
        db,
        "user.role_changed",
        {"user_id": user.id, "role": user.role},
    )
    db.commit()
    return _redirect("/admin/users", f"Role {user.username} diperbarui.")


# ---------------------------------------------------------------------------
# Master data: lembaga, fakultas, program studi
# ---------------------------------------------------------------------------


@router.get("/master", response_class=HTMLResponse)
def master_page(
    request: Request,
    tab: str = Query(default="institutions"),
    edit_type: str = Query(default=""),
    edit_id: str = Query(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
):
    if tab not in ("institutions", "faculties", "programs"):
        tab = "institutions"

    institutions = (
        db.execute(select(Institution).order_by(Institution.name.asc()))
        .scalars()
        .all()
    )
    faculties = (
        db.execute(select(Faculty).order_by(Faculty.name.asc())).scalars().all()
    )
    programs = (
        db.execute(select(StudyProgram).order_by(StudyProgram.name.asc()))
        .scalars()
        .all()
    )

    editing = None
    if edit_id:
        model = {
            "institution": Institution,
            "faculty": Faculty,
            "program": StudyProgram,
        }.get(edit_type)
        if model is not None:
            editing = db.get(model, edit_id)

    return templates.TemplateResponse(
        request,
        "admin/master.html",
        _context(
            request,
            active="master",
            tab=tab,
            institutions=institutions,
            faculties=faculties,
            programs=programs,
            editing=editing,
            edit_type=edit_type,
        ),
    )


@router.post("/master/institution")
def master_institution_save(
    request: Request,
    institution_id: str = Form(default=""),
    name: str = Form(default=""),
    code: str = Form(default=""),
    head_name: str = Form(default=""),
    head_title: str = Form(default="Ketua"),
    address: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    target = "/admin/master?tab=institutions"
    name = name.strip()
    if not name:
        return _redirect(target, "Nama lembaga wajib diisi.", "err")

    if institution_id:
        institution = db.get(Institution, institution_id)
        if institution is None:
            return _redirect(target, "Lembaga tidak ditemukan.", "err")
    else:
        institution = Institution(name=name)
        db.add(institution)

    institution.name = name
    institution.code = code.strip() or None
    institution.head_name = head_name.strip() or None
    institution.head_title = head_title.strip() or None
    institution.address = address.strip() or None
    append_event(
        db,
        "master.institution.saved",
        {"institution_id": institution.id, "name": institution.name},
    )
    db.commit()
    return _redirect(target, "Data lembaga disimpan.")


@router.post("/master/faculty")
def master_faculty_save(
    request: Request,
    faculty_id: str = Form(default=""),
    institution_id: str = Form(default=""),
    name: str = Form(default=""),
    code: str = Form(default=""),
    head_name: str = Form(default=""),
    head_title: str = Form(default="Ketua"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    target = "/admin/master?tab=faculties"
    name = name.strip()
    if not name or not institution_id:
        return _redirect(target, "Nama fakultas dan lembaga wajib diisi.", "err")
    if db.get(Institution, institution_id) is None:
        return _redirect(target, "Lembaga tidak ditemukan.", "err")

    if faculty_id:
        faculty = db.get(Faculty, faculty_id)
        if faculty is None:
            return _redirect(target, "Fakultas tidak ditemukan.", "err")
    else:
        faculty = Faculty(institution_id=institution_id, name=name)
        db.add(faculty)

    faculty.institution_id = institution_id
    faculty.name = name
    faculty.code = code.strip() or None
    faculty.head_name = head_name.strip() or None
    faculty.head_title = head_title.strip() or None
    append_event(
        db,
        "master.faculty.saved",
        {"faculty_id": faculty.id, "name": faculty.name},
    )
    db.commit()
    return _redirect(target, "Data fakultas disimpan.")


@router.post("/master/program")
def master_program_save(
    request: Request,
    program_id: str = Form(default=""),
    faculty_id: str = Form(default=""),
    name: str = Form(default=""),
    code: str = Form(default=""),
    level: str = Form(default=""),
    head_name: str = Form(default=""),
    head_title: str = Form(default="Ketua Program Studi"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    target = "/admin/master?tab=programs"
    name = name.strip()
    if not name or not faculty_id:
        return _redirect(target, "Nama prodi dan fakultas wajib diisi.", "err")
    if db.get(Faculty, faculty_id) is None:
        return _redirect(target, "Fakultas tidak ditemukan.", "err")

    if program_id:
        program = db.get(StudyProgram, program_id)
        if program is None:
            return _redirect(target, "Program studi tidak ditemukan.", "err")
    else:
        program = StudyProgram(faculty_id=faculty_id, name=name)
        db.add(program)

    program.faculty_id = faculty_id
    program.name = name
    program.code = code.strip() or None
    program.level = level.strip() or None
    program.head_name = head_name.strip() or None
    program.head_title = head_title.strip() or None
    append_event(
        db,
        "master.program.saved",
        {"program_id": program.id, "name": program.name},
    )
    db.commit()
    return _redirect(target, "Data program studi disimpan.")


@router.post("/master/{kind}/{obj_id}/delete")
def master_delete(
    request: Request,
    kind: str,
    obj_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    targets = {
        "institution": ("institutions", Institution),
        "faculty": ("faculties", Faculty),
        "program": ("programs", StudyProgram),
    }
    if kind not in targets:
        raise HTTPException(status_code=404, detail="Jenis master tidak dikenal.")
    tab, model = targets[kind]
    obj = db.get(model, obj_id)
    if obj is None:
        return _redirect(f"/admin/master?tab={tab}", "Data tidak ditemukan.", "err")

    if isinstance(obj, Institution):
        used = (
            db.scalar(
                select(func.count())
                .select_from(Faculty)
                .where(Faculty.institution_id == obj.id)
            )
            or 0
        ) + (
            db.scalar(
                select(func.count())
                .select_from(LetterRequest)
                .where(LetterRequest.institution_id == obj.id)
            )
            or 0
        )
    elif isinstance(obj, Faculty):
        used = (
            db.scalar(
                select(func.count())
                .select_from(StudyProgram)
                .where(StudyProgram.faculty_id == obj.id)
            )
            or 0
        ) + (
            db.scalar(
                select(func.count())
                .select_from(LetterRequest)
                .where(LetterRequest.faculty_id == obj.id)
            )
            or 0
        )
    else:
        used = (
            db.scalar(
                select(func.count())
                .select_from(LetterRequest)
                .where(LetterRequest.program_id == obj.id)
            )
            or 0
        )

    if used:
        return _redirect(
            f"/admin/master?tab={tab}",
            "Data masih dipakai oleh fakultas/prodi/permohonan.",
            "err",
        )

    append_event(db, f"master.{kind}.deleted", {"id": obj.id, "name": obj.name})
    db.delete(obj)
    db.commit()
    return _redirect(f"/admin/master?tab={tab}", "Data master dihapus.")


# ---------------------------------------------------------------------------
# Permintaan surat (admin TU)
# ---------------------------------------------------------------------------


@router.get("/letters", response_class=HTMLResponse)
def letters_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    conditions = []
    query_text = q.strip()
    if query_text:
        like = f"%{query_text}%"
        conditions.append(
            or_(
                LetterRequest.applicant_name.ilike(like),
                LetterRequest.nim.ilike(like),
                LetterRequest.request_no.ilike(like),
                LetterRequest.letter_no.ilike(like),
            )
        )
    if status in LETTER_STATUSES:
        conditions.append(LetterRequest.status == status)

    total = (
        db.scalar(
            select(func.count()).select_from(LetterRequest).where(*conditions)
        )
        or 0
    )
    pagination = _paginate(request, page=page, total=total)
    letters = (
        db.execute(
            select(LetterRequest)
            .where(*conditions)
            .order_by(LetterRequest.submitted_at.desc())
            .offset((pagination["page"] - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        .scalars()
        .all()
    )
    counts = {
        key: db.scalar(
            select(func.count())
            .select_from(LetterRequest)
            .where(LetterRequest.status == key)
        )
        or 0
        for key in LETTER_STATUSES
    }
    return templates.TemplateResponse(
        request,
        "admin/letters.html",
        _context(
            request,
            active="letters",
            letters=letters,
            q=query_text,
            status=status,
            statuses=LETTER_STATUSES,
            counts=counts,
            pagination=pagination,
        ),
    )


@router.get("/letters/{letter_id}", response_class=HTMLResponse)
def letter_detail(
    request: Request,
    letter_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    letter = db.get(LetterRequest, letter_id)
    if letter is None:
        raise HTTPException(status_code=404, detail="Permintaan surat tidak ditemukan.")
    document = db.get(Document, letter.document_id) if letter.document_id else None
    if (
        document is not None
        and document.status == "signed"
        and letter.status == "approved"
    ):
        letter.status = "signed"
        append_event(
            db,
            "letter.signed",
            {"letter_id": letter.id, "document_id": document.id},
        )
        db.commit()
    return templates.TemplateResponse(
        request,
        "admin/letter_detail.html",
        _context(
            request,
            active="letters",
            letter=letter,
            document=document,
            statuses=LETTER_STATUSES,
            current_path=f"/admin/letters/{letter.id}",
        ),
    )


@router.post("/letters/{letter_id}/verify")
def letter_verify(
    request: Request,
    letter_id: str,
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/letters/{letter_id}")
    letter = db.get(LetterRequest, letter_id)
    if letter is None:
        return _redirect(target, "Permintaan tidak ditemukan.", "err")
    if letter.status != "submitted":
        return _redirect(target, "Hanya permohonan berstatus Diajukan yang dapat diverifikasi.", "err")
    letter.status = "verified"
    letter.verified_at = utcnow()
    letter.verified_by = admin.name
    append_event(
        db,
        "letter.verified",
        {"letter_id": letter.id, "request_no": letter.request_no},
    )
    db.commit()
    return _redirect(target, "Permohonan diverifikasi.")


@router.post("/letters/{letter_id}/approve")
def letter_approve(
    request: Request,
    letter_id: str,
    letter_no: str = Form(default=""),
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/letters/{letter_id}")
    letter = db.get(LetterRequest, letter_id)
    if letter is None:
        return _redirect(target, "Permintaan tidak ditemukan.", "err")
    if letter.status not in ("submitted", "verified"):
        return _redirect(target, "Status permohonan tidak dapat disetujui.", "err")
    letter_no = letter_no.strip()
    if not letter_no:
        return _redirect(target, "Nomor surat wajib diisi.", "err")
    duplicate = db.execute(
        select(LetterRequest).where(LetterRequest.letter_no == letter_no).limit(1)
    ).scalar_one_or_none()
    if duplicate is not None and duplicate.id != letter.id:
        return _redirect(target, "Nomor surat sudah dipakai.", "err")
    letter.letter_no = letter_no
    letter.status = "approved"
    letter.approved_at = utcnow()
    letter.approved_by = admin.name
    append_event(
        db,
        "letter.approved",
        {
            "letter_id": letter.id,
            "request_no": letter.request_no,
            "letter_no": letter_no,
        },
    )
    db.commit()
    return _redirect(target, "Permohonan disetujui dan nomor surat terbit.")


@router.post("/letters/{letter_id}/reject")
def letter_reject(
    request: Request,
    letter_id: str,
    reason: str = Form(default=""),
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/letters/{letter_id}")
    letter = db.get(LetterRequest, letter_id)
    if letter is None:
        return _redirect(target, "Permintaan tidak ditemukan.", "err")
    if letter.status in ("signed", "rejected"):
        return _redirect(target, "Status permohonan tidak dapat ditolak.", "err")
    reason = reason.strip()
    if not reason:
        return _redirect(target, "Alasan penolakan wajib diisi.", "err")
    letter.status = "rejected"
    letter.rejection_reason = reason
    letter.rejected_at = utcnow()
    append_event(
        db,
        "letter.rejected",
        {
            "letter_id": letter.id,
            "request_no": letter.request_no,
            "reason": reason,
            "by": admin.name,
        },
    )
    db.commit()
    return _redirect(target, "Permohonan ditolak.")


@router.post("/letters/{letter_id}/document")
async def letter_upload_document(
    request: Request,
    letter_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = f"/admin/letters/{letter_id}"
    letter = db.get(LetterRequest, letter_id)
    if letter is None:
        return _redirect("/admin/letters", "Permintaan tidak ditemukan.", "err")
    if letter.status not in ("approved", "signed"):
        return _redirect(
            target, "Setujui permohonan sebelum mengunggah PDF final.", "err"
        )

    data = await file.read()
    try:
        doc = upload_document_op(
            db,
            data=data,
            filename=file.filename or f"surat-{letter.request_no}.pdf",
        )
    except OperationError as exc:
        return _redirect(target, exc.message, "err")

    letter.document_id = doc.id
    append_event(
        db,
        "letter.document_uploaded",
        {
            "letter_id": letter.id,
            "document_id": doc.id,
            "short_id": doc.short_id,
        },
    )
    db.commit()
    return _redirect(
        f"/admin/documents/{doc.id}",
        "PDF final terhubung. Lanjutkan pembuatan permintaan TTE di halaman dokumen.",
    )
