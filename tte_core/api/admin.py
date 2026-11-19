from __future__ import annotations

import hmac
import json
import re
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
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..admin_auth import (
    ADMIN_COOKIE,
    CONFIGURABLE_ROLES,
    FEATURES,
    ROLE_LABELS,
    load_permissions,
    require_permission,
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
from ..mailer import (
    MailError,
    get_email_config,
    send_email,
    send_letter_email,
    store_email_password,
)
from ..letter_docs import (
    KEYWORD_GROUPS,
    build_context,
    FONT_CHOICES,
    get_active_letterhead,
    get_active_style,
    logo_file,
    render_letter_html,
    render_pdf,
    render_preview_html,
    render_head_preview,
    render_text,
    sample_context,
)
from ..models import (
    AuditEvent,
    Document,
    DriveFolder,
    EmailLog,
    EmailSettings,
    Faculty,
    Institution,
    LetterHead,
    LetterRequest,
    LetterStyle,
    LetterTemplate,
    StorageSettings,
    SignatureRecord,
    RolePermission,
    SignerProfile,
    SignRequest,
    StudyProgram,
    User,
    VerificationToken,
    utcnow,
)
from ..drive import (
    DriveError,
    delete_file as drive_delete_file,
    ensure_document_file,
    folder_for_ref as drive_folder_for_ref,
    get_drive_config,
    store_credentials,
    test_connection as drive_test_connection,
)
from ..pki import PkiError
from ..signer_certs import (
    delete_signer_certificate,
    issue_signer_certificate,
)
from ..operations import (
    OperationError,
    create_sign_request as create_sign_request_op,
    finalize_letter_if_complete,
    letter_signatures_complete,
    grant_consent as grant_consent_op,
    sign_request as sign_request_op,
    upload_document as upload_document_op,
)
from ..security import hash_password, verify_password
from .. import scope as scope_mod
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

USER_ROLES = tuple(ROLE_LABELS)
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
        "permissions": (
            set(getattr(request.state.user, "permissions", ()))
            if getattr(request.state, "user", None)
            else set()
        ),
        "role_labels": ROLE_LABELS,
    }
    context.update(extra)
    return context


def _redirect(
    url: str, msg: str | None = None, kind: str = "ok"
) -> RedirectResponse:
    fragment = ""
    if "#" in url:
        url, _, anchor = url.partition("#")
        fragment = f"#{anchor}"
    if msg:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'msg': msg, 'kind': kind})}"
    return RedirectResponse(url=url + fragment, status_code=303)


def _deny_letter(request: Request, letter: LetterRequest):
    user = getattr(request.state, "user", None)
    if user is not None and not scope_mod.can_access_letter(user, letter):
        return _redirect(
            "/admin/letters",
            "Anda tidak memiliki akses ke permohonan ini.",
            "err",
        )
    return None


def _deny_document(request: Request, db: Session, doc: Document):
    user = getattr(request.state, "user", None)
    if user is not None and not scope_mod.can_access_document(db, user, doc):
        return _redirect(
            "/admin/documents",
            "Anda tidak memiliki akses ke dokumen ini.",
            "err",
        )
    return None


def _doc_anchor(url: str) -> str:
    """Arahkan kembali ke panel aksi tanda tangan pada halaman dokumen."""
    if url.startswith("/admin/documents/") and "#" not in url:
        return f"{url}#permintaan-ttd"
    return url


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

    dash_user = getattr(request.state, "user", None)
    dash_letter_scope = scope_mod.letter_condition(dash_user)
    dash_doc_scope = scope_mod.document_condition(dash_user)
    dash_req_scope = scope_mod.request_condition(dash_user, SignRequest)

    def _scoped(condition, *extra):
        return [item for item in (condition, *extra) if item is not None]

    letter_pending = (
        db.scalar(
            select(func.count())
            .select_from(LetterRequest)
            .where(
                *_scoped(
                    dash_letter_scope,
                    LetterRequest.status.in_(("submitted", "verified")),
                )
            )
        )
        or 0
    )
    letter_approved = (
        db.scalar(
            select(func.count())
            .select_from(LetterRequest)
            .where(
                *_scoped(
                    dash_letter_scope,
                    LetterRequest.status.in_(("approved", "signed")),
                )
            )
        )
        or 0
    )
    recent_letters = (
        db.execute(
            select(LetterRequest)
            .where(*_scoped(dash_letter_scope))
            .order_by(LetterRequest.submitted_at.desc())
            .limit(6)
        )
        .scalars()
        .all()
    )

    chain_ok, chain_count = verify_chain(db)
    recent_documents = (
        db.execute(
            select(Document)
            .where(*_scoped(dash_doc_scope))
            .order_by(Document.created_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    recent_requests = (
        db.execute(
            select(SignRequest)
            .where(*_scoped(dash_req_scope))
            .order_by(SignRequest.created_at.desc())
            .limit(8)
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
    _admin: str = Depends(require_permission("documents")),
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
    doc_scope = scope_mod.document_condition(
        getattr(request.state, "user", None)
    )
    if doc_scope is not None:
        conditions.append(doc_scope)

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
    _admin: str = Depends(require_permission("documents")),
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
    signer: str = Query(default=""),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_permission("documents")),
):
    doc = db.get(Document, document_id)
    _denied = _deny_document(request, db, doc)
    if _denied is not None:
        return _denied
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
            preselect_signer_id=signer,
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
    _admin=Depends(require_permission("documents")),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, "/admin/documents")
    doc = db.get(Document, document_id)
    _denied = _deny_document(request, db, doc)
    if _denied is not None:
        return _denied
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

    config = get_drive_config(db)
    drive_delete_file(config, doc.drive_original_id or "")
    drive_delete_file(config, doc.drive_signed_id or "")

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
    request: Request,
    document_id: str,
    variant: str = Query(default="signed"),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_permission("documents")),
):
    doc = db.get(Document, document_id)
    _denied = _deny_document(request, db, doc)
    if _denied is not None:
        return _denied
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
    if variant not in _DOWNLOAD_VARIANTS:
        raise HTTPException(status_code=400, detail="Varian berkas tidak dikenal.")

    if variant == "signed" and not doc.signed_path and not doc.drive_signed_id:
        raise HTTPException(status_code=404, detail="PDF bertanda tangan belum ada.")
    path = ensure_document_file(db, doc, variant)
    filename = (
        f"{Path(doc.filename).stem}-tte.pdf"
        if variant == "signed"
        else Path(doc.filename).name
    )
    if path is None or not path.exists():
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
    _admin: str = Depends(require_permission("documents")),
    _csrf: None = Depends(require_csrf),
):
    target = _doc_anchor(_safe_next(next, f"/admin/documents/{document_id}"))
    doc = db.get(Document, document_id)
    if doc is None:
        return _redirect(target, "Dokumen tidak ditemukan.", "err")
    _denied = _deny_document(request, db, doc)
    if _denied is not None:
        return _denied
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
    _admin: str = Depends(require_permission("signers")),
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
    _admin: str = Depends(require_permission("signers")),
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
    _admin=Depends(require_permission("signers")),
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
    _admin=Depends(require_permission("signers")),
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
    _admin=Depends(require_permission("signers")),
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
    _admin: str = Depends(require_permission("signers")),
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
    _admin: str = Depends(require_permission("requests")),
):
    conditions = []
    if status in REQUEST_STATUSES:
        conditions.append(SignRequest.status == status)
    req_scope = scope_mod.request_condition(
        getattr(request.state, "user", None), SignRequest
    )
    if req_scope is not None:
        conditions.append(req_scope)

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
    _admin: str = Depends(require_permission("requests")),
    _csrf: None = Depends(require_csrf),
):
    target = _doc_anchor(_safe_next(next, "/admin/requests"))
    sign_req = db.get(SignRequest, request_id)
    if sign_req is not None:
        _doc = db.get(Document, sign_req.document_id)
        if _doc is not None:
            _denied = _deny_document(request, db, _doc)
            if _denied is not None:
                return _denied
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
    _admin: str = Depends(require_permission("requests")),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, "/admin/requests")
    sign_req = db.get(SignRequest, request_id)
    if sign_req is not None:
        _doc = db.get(Document, sign_req.document_id)
        if _doc is not None:
            _denied = _deny_document(request, db, _doc)
            if _denied is not None:
                return _denied
    try:
        result = sign_request_op(db, request_id=request_id)
    except OperationError as exc:
        return _redirect(target, exc.message, "err")

    letter = finalize_letter_if_complete(db, result.document_id)
    note = ""
    if letter is not None:
        note = " Semua penandatangan selesai; surat terbit dan email dikirim."
    elif not letter_signatures_complete(db, result.document_id):
        note = " Menunggu penanda tangan lainnya."

    return _redirect(
        f"/admin/documents/{result.document_id}#permintaan-ttd",
        f"Dokumen ditandatangani ({result.pades_level}).{note}",
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    event_type: str = Query(default=""),
    page: int = Query(default=1),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_permission("audit")),
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
    _admin: str = Depends(require_permission("documents")),
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
    faculties = (
        db.execute(select(Faculty).order_by(Faculty.name.asc())).scalars().all()
    )
    programs = (
        db.execute(select(StudyProgram).order_by(StudyProgram.name.asc()))
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
            faculties=faculties,
            programs=programs,
            faculty_map={row.id: row for row in faculties},
            program_map={row.id: row for row in programs},
        ),
    )


@router.post("/users")
def users_create(
    request: Request,
    name: str = Form(default=""),
    username: str = Form(default=""),
    email: str = Form(default=""),
    role: str = Form(default="operator"),
    faculty_id: str = Form(default=""),
    program_id: str = Form(default=""),
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
    unit_faculty, unit_program, unit_error = _resolve_unit(
        db, role, faculty_id, program_id
    )
    if unit_error:
        return _redirect(target, unit_error, "err")

    user = User(
        name=name,
        username=username,
        email=email or None,
        role=role,
        faculty_id=unit_faculty,
        program_id=unit_program,
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
    faculty_id: str = Form(default=""),
    program_id: str = Form(default=""),
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
    unit_faculty, unit_program, unit_error = _resolve_unit(
        db, role, faculty_id, program_id
    )
    if unit_error:
        return _redirect("/admin/users", unit_error, "err")

    user.name = name
    user.username = username
    user.email = email or None
    user.role = role
    user.faculty_id = unit_faculty
    user.program_id = unit_program
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
    faculty_id: str = Form(default=""),
    program_id: str = Form(default=""),
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
    _admin=Depends(require_permission("master")),
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
    _admin=Depends(require_permission("master")),
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
    _admin=Depends(require_permission("master")),
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
    _admin=Depends(require_permission("master")),
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
    _admin=Depends(require_permission("master")),
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
    _admin=Depends(require_permission("letters")),
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
    letter_scope = scope_mod.letter_condition(
        getattr(request.state, "user", None)
    )
    if letter_scope is not None:
        conditions.append(letter_scope)

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
    _admin=Depends(require_permission("letters")),
):
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
    if letter is None:
        raise HTTPException(status_code=404, detail="Permintaan surat tidak ditemukan.")
    document = db.get(Document, letter.document_id) if letter.document_id else None
    if document is not None and letter.status == "approved":
        # Data lama: finalisasi bila seluruh tanda tangan sudah lengkap.
        finalize_letter_if_complete(db, document.id)
    return templates.TemplateResponse(
        request,
        "admin/letter_detail.html",
        _context(
            request,
            active="letters",
            letter=letter,
            document=document,
            statuses=LETTER_STATUSES,
            signers=_active_signers(db),
            template=_template_for_letter(db, letter),
            letter_signer=(
                db.get(SignerProfile, letter.signer_id)
                if letter.signer_id
                else None
            ),
            current_path=f"/admin/letters/{letter.id}",
        ),
    )


@router.post("/letters/{letter_id}/verify")
def letter_verify(
    request: Request,
    letter_id: str,
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    admin=Depends(require_permission("letters")),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/letters/{letter_id}")
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
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
    admin=Depends(require_permission("letters")),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/letters/{letter_id}")
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
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

    config = get_email_config(db)
    document = db.get(Document, letter.document_id) if letter.document_id else None
    note, kind = "", "ok"
    if config.on_approve:
        log = send_letter_email(
            db, letter, config, kind="approved", document=document
        )
        if log is not None and log.status == "sent":
            note = " Email pemberitahuan terkirim ke pemohon."
        elif log is not None:
            note = f" Email gagal dikirim: {log.error}"
            kind = "warn"

    return _redirect(
        target, "Permohonan disetujui dan nomor surat terbit." + note, kind
    )


@router.post("/letters/{letter_id}/reject")
def letter_reject(
    request: Request,
    letter_id: str,
    reason: str = Form(default=""),
    next: str = Form(default=""),
    db: Session = Depends(get_db),
    admin=Depends(require_permission("letters")),
    _csrf: None = Depends(require_csrf),
):
    target = _safe_next(next, f"/admin/letters/{letter_id}")
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
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
    _admin=Depends(require_permission("letters")),
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


# ---------------------------------------------------------------------------
# Format surat & kop surat
# ---------------------------------------------------------------------------


def _resolve_unit(
    db: Session, role: str, faculty_id: str, program_id: str
) -> tuple[str | None, str | None, str | None]:
    """Validasi unit untuk role; kembalikan (faculty_id, program_id, error)."""
    if role == "operator_fakultas":
        faculty = db.get(Faculty, faculty_id) if faculty_id else None
        if faculty is None:
            return None, None, "Pilih fakultas untuk Operator Fakultas."
        return faculty.id, None, None
    if role == "operator_prodi":
        program = db.get(StudyProgram, program_id) if program_id else None
        if program is None:
            return None, None, "Pilih program studi untuk Operator Prodi."
        return program.faculty_id, program.id, None
    return None, None, None


def _active_signers(db: Session) -> list[SignerProfile]:
    return list(
        db.execute(
            select(SignerProfile)
            .where(SignerProfile.status == "active")
            .order_by(SignerProfile.name.asc())
        )
        .scalars()
        .all()
    )


def _template_rows(db: Session) -> list[LetterTemplate]:
    return list(
        db.execute(
            select(LetterTemplate).order_by(LetterTemplate.letter_type.asc())
        )
        .scalars()
        .all()
    )


def _template_for_letter(
    db: Session, letter: LetterRequest
) -> LetterTemplate | None:
    if letter.template_id:
        template = db.get(LetterTemplate, letter.template_id)
        if template is not None:
            return template
    return db.execute(
        select(LetterTemplate).where(LetterTemplate.letter_type == letter.letter_type)
    ).scalar_one_or_none()


def _drive_folders(db: Session) -> list[DriveFolder]:
    return list(
        db.execute(
            select(DriveFolder).order_by(DriveFolder.name.asc())
        ).scalars()
    )


@router.get("/letter-templates", response_class=HTMLResponse)
def letter_templates_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    signers = _active_signers(db)
    return templates.TemplateResponse(
        request,
        "admin/letter_templates.html",
        _context(
            request,
            active="templates",
            active_tab="templates",
            rows=_template_rows(db),
            signers=signers,
            signer_map={row.id: row for row in signers},
            letterhead=get_active_letterhead(db),
            drive_folders=_drive_folders(db),
        ),
    )


@router.get("/letter-templates/new", response_class=HTMLResponse)
def letter_template_new(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    return templates.TemplateResponse(
        request,
        "admin/letter_template_form.html",
        _context(
            request,
            active="templates",
            editing=None,
            signers=_active_signers(db),
            keyword_groups=KEYWORD_GROUPS,
            letterhead=get_active_letterhead(db),
            drive_folders=_drive_folders(db),
        ),
    )


@router.get("/letter-templates/{template_id}", response_class=HTMLResponse)
def letter_template_edit(
    request: Request,
    template_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    template = db.get(LetterTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Format surat tidak ditemukan.")
    return templates.TemplateResponse(
        request,
        "admin/letter_template_form.html",
        _context(
            request,
            active="templates",
            editing=template,
            signers=_active_signers(db),
            keyword_groups=KEYWORD_GROUPS,
            letterhead=get_active_letterhead(db),
            drive_folders=_drive_folders(db),
        ),
    )


@router.post("/letter-templates/save")
def letter_template_save(
    request: Request,
    letter_type: str = Form(default=""),
    name: str = Form(default=""),
    body_html: str = Form(default=""),
    signer_id: str = Form(default=""),
    drive_folder_ref: str = Form(default=""),
    is_active: str = Form(default=""),
    template_id: str = Form(default=""),
    db: Session = Depends(get_db),
    admin=Depends(require_permission("templates")),
    _csrf: None = Depends(require_csrf),
):
    letter_type = letter_type.strip()
    name = name.strip()
    if not letter_type or not name:
        return _redirect(
            "/admin/letter-templates", "Jenis surat dan nama format wajib diisi.", "err"
        )
    if not (body_html or "").strip():
        return _redirect(
            "/admin/letter-templates", "Isi format surat tidak boleh kosong.", "err"
        )

    duplicate = db.execute(
        select(LetterTemplate).where(LetterTemplate.letter_type == letter_type)
    ).scalar_one_or_none()

    template = db.get(LetterTemplate, template_id) if template_id else None
    if template is None:
        template = duplicate
    if duplicate is not None and duplicate is not template:
        return _redirect(
            "/admin/letter-templates",
            f"Jenis surat '{letter_type}' sudah punya format. Edit format tersebut.",
            "err",
        )

    signer = db.get(SignerProfile, signer_id) if signer_id else None
    if template is None:
        template = LetterTemplate(
            letter_type=letter_type,
            created_by=admin.name,
        )
        db.add(template)

    template.letter_type = letter_type
    template.name = name
    template.body_html = body_html
    template.signer_id = signer.id if signer else None
    folder = db.get(DriveFolder, drive_folder_ref) if drive_folder_ref else None
    template.drive_folder_ref = folder.id if folder else None
    template.is_active = bool(is_active)
    template.updated_at = utcnow()

    append_event(
        db,
        "letter_template.saved",
        {
            "template_id": template.id,
            "letter_type": letter_type,
            "active": template.is_active,
        },
    )
    db.commit()
    return _redirect(
        "/admin/letter-templates", f"Format '{letter_type}' disimpan."
    )


@router.post("/letter-templates/{template_id}/toggle")
def letter_template_toggle(
    template_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
    _csrf: None = Depends(require_csrf),
):
    template = db.get(LetterTemplate, template_id)
    if template is None:
        return _redirect("/admin/letter-templates", "Format tidak ditemukan.", "err")
    template.is_active = not template.is_active
    template.updated_at = utcnow()
    append_event(
        db,
        "letter_template.toggled",
        {"template_id": template.id, "active": template.is_active},
    )
    db.commit()
    state = "diaktifkan" if template.is_active else "dinonaktifkan"
    return _redirect(
        "/admin/letter-templates", f"Format '{template.letter_type}' {state}."
    )


@router.post("/letter-templates/{template_id}/delete")
def letter_template_delete(
    template_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
    _csrf: None = Depends(require_csrf),
):
    template = db.get(LetterTemplate, template_id)
    if template is None:
        return _redirect("/admin/letter-templates", "Format tidak ditemukan.", "err")
    letter_type = template.letter_type
    # Permohonan lama tetap aman: letter_type disimpan sebagai teks.
    for letter in db.execute(
        select(LetterRequest).where(LetterRequest.template_id == template.id)
    ).scalars():
        letter.template_id = None
    append_event(
        db,
        "letter_template.deleted",
        {"template_id": template.id, "letter_type": letter_type},
    )
    db.delete(template)
    db.commit()
    return _redirect(
        "/admin/letter-templates", f"Format '{letter_type}' dihapus."
    )


@router.post("/letter-templates/preview")
def letter_template_preview(
    request: Request,
    body_html: str = Form(default=""),
    letter_id: str = Form(default=""),
    signer_id: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
    _csrf: None = Depends(require_csrf),
):
    settings = get_settings()
    signer = db.get(SignerProfile, signer_id) if signer_id else None
    letter = db.get(LetterRequest, letter_id) if letter_id else None
    if letter is not None:
        _user = getattr(request.state, "user", None)
        if _user is not None and not scope_mod.can_access_letter(_user, letter):
            return JSONResponse(
                {"html": "", "unknown": [], "error": "Tidak ada akses."},
                status_code=403,
            )
    if letter is not None:
        if signer is None and letter.signer_id:
            signer = db.get(SignerProfile, letter.signer_id)
        context = build_context(letter, signer, settings)
    else:
        context = sample_context(settings)

    document, unknown = render_preview_html(
        body_html,
        context,
        get_active_letterhead(db),
        logo_src="/admin/letterhead/logo",
        style=get_active_style(db),
    )
    return JSONResponse({"html": document, "unknown": unknown})


@router.get("/letterhead", response_class=HTMLResponse)
def letterhead_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    return templates.TemplateResponse(
        request,
        "admin/letterhead.html",
        _context(
            request,
            active="templates",
            active_tab="head",
            letterhead=get_active_letterhead(db),
        ),
    )


@router.post("/letterhead")
async def letterhead_save(
    request: Request,
    name: str = Form(default="Kop Surat Utama"),
    line1: str = Form(default=""),
    line2: str = Form(default=""),
    line3: str = Form(default=""),
    line4: str = Form(default=""),
    line5: str = Form(default=""),
    accent_color: str = Form(default=""),
    html_override: str = Form(default=""),
    logo: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
    _csrf: None = Depends(require_csrf),
):
    settings = get_settings()
    letterhead = get_active_letterhead(db)
    if letterhead is None:
        letterhead = LetterHead(is_active=True)
        db.add(letterhead)

    letterhead.name = name.strip() or "Kop Surat Utama"
    letterhead.line1 = line1.strip() or None
    letterhead.line2 = line2.strip() or None
    letterhead.line3 = line3.strip() or None
    letterhead.line4 = line4.strip() or None
    letterhead.line5 = line5.strip() or None
    color = accent_color.strip() or "#0d6b3f"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#0d6b3f"
    letterhead.accent_color = color
    letterhead.html_override = (html_override or "").strip() or None
    letterhead.updated_at = utcnow()

    if logo is not None and logo.filename:
        suffix = Path(logo.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return _redirect(
                "/admin/letterhead", "Logo harus berupa gambar PNG/JPG.", "err"
            )
        directory = settings.data_dir / "letterheads"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"logo{suffix}"
        data = await logo.read()
        if len(data) > 2 * 1024 * 1024:
            return _redirect("/admin/letterhead", "Ukuran logo maksimal 2 MB.", "err")
        target.write_bytes(data)
        letterhead.logo_path = str(target)

    append_event(db, "letterhead.saved", {"letterhead_id": letterhead.id})
    db.commit()
    return _redirect("/admin/letterhead", "Kop surat disimpan.")


@router.get("/letterhead/logo")
def letterhead_logo(
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    path = logo_file(get_active_letterhead(db))
    if path is None:
        raise HTTPException(status_code=404, detail="Logo belum tersedia.")
    return FileResponse(path)


@router.get("/letterhead/preview", response_class=HTMLResponse)
def letterhead_preview(
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    return HTMLResponse(
        render_head_preview(
            get_active_letterhead(db),
            logo_src="/admin/letterhead/logo",
            style=get_active_style(db),
        )
    )


def _remove_document(db: Session, doc: Document) -> None:
    """Hapus dokumen lama saat format surat digenerate ulang."""
    settings = get_settings()
    for token in db.execute(
        select(VerificationToken).where(VerificationToken.document_id == doc.id)
    ).scalars():
        db.delete(token)
    db.delete(doc)
    db.commit()
    shutil.rmtree(settings.documents_dir / doc.id, ignore_errors=True)


@router.post("/letters/{letter_id}/generate")
def letter_generate(
    request: Request,
    letter_id: str,
    signer_id: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("letters")),
    _csrf: None = Depends(require_csrf),
):
    target = f"/admin/letters/{letter_id}"
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
    if letter is None:
        return _redirect("/admin/letters", "Permintaan tidak ditemukan.", "err")
    if letter.status != "approved":
        return _redirect(
            target, "Setujui permohonan sebelum membuat PDF surat.", "err"
        )

    template = _template_for_letter(db, letter)
    if template is None:
        return _redirect(
            target, "Format surat untuk jenis ini belum tersedia.", "err"
        )

    signer = db.get(SignerProfile, signer_id) if signer_id else None
    if signer is None and template.signer_id:
        signer = db.get(SignerProfile, template.signer_id)
    if signer is None:
        active = _active_signers(db)
        signer = active[0] if active else None
    if signer is None:
        return _redirect(
            target, "Tambahkan penanda tangan aktif terlebih dahulu.", "err"
        )

    existing = db.get(Document, letter.document_id) if letter.document_id else None
    if existing is not None and existing.status == "signed":
        return _redirect(
            target, "Dokumen sudah ditandatangani dan tidak dapat dibuat ulang.", "err"
        )

    settings = get_settings()
    document_html, unknown = render_letter_html(
        letter=letter,
        template=template,
        letterhead=get_active_letterhead(db),
        signer=signer,
        settings=settings,
        style=get_active_style(db),
    )
    if unknown:
        listing = ", ".join(f"[{item}]" for item in unknown[:8])
        return _redirect(
            target,
            f"Keyword tidak dikenal: {listing}. Perbaiki format surat dahulu.",
            "err",
        )

    try:
        pdf_bytes = render_pdf(document_html)
    except Exception as exc:  # noqa: BLE001
        return _redirect(target, f"Gagal membuat PDF: {exc}", "err")

    if existing is not None:
        _remove_document(db, existing)
        letter.document_id = None
        db.flush()

    filename = f"{letter.letter_type} - {letter.applicant_name}.pdf"
    drive_folder = drive_folder_for_ref(db, template.drive_folder_ref) or None
    doc = upload_document_op(
        db, data=pdf_bytes, filename=filename, drive_folder_id=drive_folder
    )
    letter.document_id = doc.id
    letter.signer_id = signer.id
    letter.template_id = template.id
    append_event(
        db,
        "letter.generated",
        {
            "letter_id": letter.id,
            "document_id": doc.id,
            "template_id": template.id,
            "signer_id": signer.id,
        },
    )
    db.commit()
    return _redirect(
        f"/admin/documents/{doc.id}?signer={signer.id}",
        "PDF surat dibuat. Email dikirim otomatis setelah semua "
        "penandatangan menyelesaikan TTE.",
    )


@router.get("/letters/{letter_id}/preview", response_class=HTMLResponse)
def letter_preview(
    request: Request,
    letter_id: str,
    template_id: str = Query(default=""),
    signer_id: str = Query(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("letters")),
):
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
    if letter is None:
        raise HTTPException(status_code=404, detail="Permintaan tidak ditemukan.")
    template = (
        db.get(LetterTemplate, template_id)
        if template_id
        else _template_for_letter(db, letter)
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Format surat belum tersedia.")
    settings = get_settings()
    signer = db.get(SignerProfile, signer_id) if signer_id else None
    if signer is None and letter.signer_id:
        signer = db.get(SignerProfile, letter.signer_id)
    document_html, _unknown = render_letter_html(
        letter=letter,
        template=template,
        letterhead=get_active_letterhead(db),
        signer=signer,
        settings=settings,
        logo_src="/admin/letterhead/logo",
        style=get_active_style(db),
    )
    return HTMLResponse(document_html)


@router.get("/letter-style", response_class=HTMLResponse)
def letter_style_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    return templates.TemplateResponse(
        request,
        "admin/letter_style.html",
        _context(
            request,
            active="templates",
            active_tab="style",
            style=get_active_style(db),
            font_choices=FONT_CHOICES,
        ),
    )


def _float_field(value: str, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


@router.post("/letter-style")
def letter_style_save(
    request: Request,
    body_font: str = Form(default="Times New Roman"),
    body_size_pt: str = Form(default="12"),
    line_height: str = Form(default="1.55"),
    paragraph_gap_pt: str = Form(default="10"),
    margin_top_cm: str = Form(default="2"),
    margin_bottom_cm: str = Form(default="2.2"),
    margin_left_cm: str = Form(default="2.6"),
    margin_right_cm: str = Form(default="2.4"),
    kop_gap_mm: str = Form(default="4"),
    kop_line1_size_pt: str = Form(default="11.5"),
    kop_main_size_pt: str = Form(default="14"),
    kop_address_size_pt: str = Form(default="8.5"),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
    _csrf: None = Depends(require_csrf),
):
    style = (
        db.execute(
            select(LetterStyle).order_by(LetterStyle.updated_at.desc()).limit(1)
        ).scalar_one_or_none()
    )
    if style is None:
        style = LetterStyle()
        db.add(style)

    style.body_font = (
        body_font.strip() if body_font.strip() in FONT_CHOICES else "Times New Roman"
    )
    style.body_size_pt = _float_field(body_size_pt, 12, 8, 24)
    style.line_height = _float_field(line_height, 1.55, 1, 3)
    style.paragraph_gap_pt = _float_field(paragraph_gap_pt, 10, 0, 30)
    style.margin_top_cm = _float_field(margin_top_cm, 2, 1, 5)
    style.margin_bottom_cm = _float_field(margin_bottom_cm, 2.2, 1, 5)
    style.margin_left_cm = _float_field(margin_left_cm, 2.6, 1, 5)
    style.margin_right_cm = _float_field(margin_right_cm, 2.4, 1, 5)
    style.kop_gap_mm = _float_field(kop_gap_mm, 4, 0, 20)
    style.kop_line1_size_pt = _float_field(kop_line1_size_pt, 11.5, 6, 24)
    style.kop_main_size_pt = _float_field(kop_main_size_pt, 14, 6, 30)
    style.kop_address_size_pt = _float_field(kop_address_size_pt, 8.5, 5, 16)
    style.updated_at = utcnow()

    append_event(
        db,
        "letter_style.saved",
        {
            "body_font": style.body_font,
            "body_size_pt": style.body_size_pt,
            "line_height": style.line_height,
        },
    )
    db.commit()
    return _redirect("/admin/letter-style", "Pengaturan tipografi disimpan.")


@router.get("/letter-style/preview", response_class=HTMLResponse)
def letter_style_preview(
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("templates")),
):
    template = (
        db.execute(
            select(LetterTemplate)
            .where(LetterTemplate.is_active.is_(True))
            .order_by(LetterTemplate.letter_type.asc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    body = (
        template.body_html
        if template is not None
        else (
            "<p>Contoh isi surat dengan pengaturan tipografi yang dipilih. "
            "Paragraf ini menunjukkan ukuran huruf, jarak antarbaris, dan "
            "lebar margin halaman.</p>"
            "<p>Demikian contoh ini dibuat untuk melihat hasil akhir sebelum "
            "surat diterbitkan.</p>"
        )
    )
    settings = get_settings()
    document, _unknown = render_preview_html(
        body,
        sample_context(settings),
        get_active_letterhead(db),
        logo_src="/admin/letterhead/logo",
        style=get_active_style(db),
    )
    return HTMLResponse(document)


@router.get("/email", response_class=HTMLResponse)
def email_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("email")),
):
    config = get_email_config(db)
    logs = (
        db.execute(
            select(EmailLog)
            .order_by(EmailLog.created_at.desc())
            .limit(25)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/email.html",
        _context(
            request,
            active="email",
            smtp=config,
            email_ready=config.ready,
            has_password=bool(config.password),
            logs=logs,
        ),
    )


@router.post("/email/save")
def email_save(
    host: str = Form(default="smtp.gmail.com"),
    port: str = Form(default="587"),
    username: str = Form(default=""),
    password: str = Form(default=""),
    from_name: str = Form(default="STIT Buntet Pesantren"),
    from_email: str = Form(default=""),
    use_ssl: str = Form(default=""),
    security: str = Form(default=""),
    timeout: str = Form(default="20"),
    enabled: str = Form(default=""),
    on_approve: str = Form(default=""),
    on_generate: str = Form(default=""),
    on_signed: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("email")),
    _csrf: None = Depends(require_csrf),
):
    row = db.execute(select(EmailSettings).limit(1)).scalar_one_or_none()
    if row is None:
        row = EmailSettings()
        db.add(row)

    row.host = host.strip() or "smtp.gmail.com"
    port_number = int(_float_field(port, 587, 1, 65535))
    ssl_mode = (security == "ssl") if security else bool(use_ssl)
    # Selaraskan port dengan mode agar tidak terjadi WRONG_VERSION_NUMBER.
    if ssl_mode and port_number == 587:
        port_number = 465
    elif not ssl_mode and port_number == 465:
        port_number = 587
    row.port = port_number
    row.username = username.strip() or None
    row.from_name = from_name.strip() or "STIT Buntet Pesantren"
    row.from_email = from_email.strip() or None
    row.use_ssl = ssl_mode
    row.timeout = int(_float_field(timeout, 20, 5, 120))
    row.enabled = bool(enabled)
    row.on_approve = bool(on_approve)
    row.on_generate = bool(on_generate)
    # Bila form baru tidak mengirim on_signed, pakai nilai lama.
    if on_signed != "":
        row.on_signed = bool(on_signed)
    row.updated_at = utcnow()

    if password.strip():
        store_email_password(db, row, password.strip())

    append_event(
        db,
        "email.settings_saved",
        {
            "host": row.host,
            "port": row.port,
            "username": row.username,
            "enabled": row.enabled,
        },
    )
    db.commit()
    return _redirect("/admin/email", "Pengaturan email disimpan.")


@router.post("/email/test")
def email_test(
    to: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("email")),
    _csrf: None = Depends(require_csrf),
):
    recipient = to.strip()
    if not recipient:
        return _redirect("/admin/email", "Alamat tujuan wajib diisi.", "err")
    config = get_email_config(db)
    try:
        send_email(
            config,
            to=recipient,
            subject="Tes SMTP — TTE STIT Buntet Pesantren",
            text=(
                "Email uji dari sistem TTE.\n\n"
                "Jika Anda menerima email ini, konfigurasi SMTP sudah benar."
            ),
        )
    except MailError as exc:
        return _redirect("/admin/email", str(exc), "err")
    return _redirect("/admin/email", f"Email uji terkirim ke {recipient}.")


@router.post("/letters/{letter_id}/email")
def letter_resend_email(
    letter_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("letters")),
    _csrf: None = Depends(require_csrf),
):
    target = f"/admin/letters/{letter_id}"
    letter = db.get(LetterRequest, letter_id)
    _denied = _deny_letter(request, letter)
    if _denied is not None:
        return _denied
    if letter is None:
        return _redirect("/admin/letters", "Permintaan tidak ditemukan.", "err")
    config = get_email_config(db)
    if not config.ready:
        return _redirect(
            target,
            "Email belum aktif. Atur akun SMTP di halaman Email.",
            "err",
        )
    document = db.get(Document, letter.document_id) if letter.document_id else None
    log = send_letter_email(
        db, letter, config, kind="manual", document=document, force=True
    )
    if log is not None and log.status == "sent":
        return _redirect(target, f"Email terkirim ke {letter.email}.")
    message = log.error if log is not None else "Email gagal dikirim."
    return _redirect(target, message, "err")


@router.get("/storage", response_class=HTMLResponse)
def storage_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("storage")),
):
    config = get_drive_config(db)
    return templates.TemplateResponse(
        request,
        "admin/storage.html",
        _context(
            request,
            active="storage",
            drive=config,
            drive_ready=config.ready,
            folders=_drive_folders(db),
            has_credentials=bool(config.credentials_json),
        ),
    )


@router.post("/storage")
async def storage_save(
    enabled: str = Form(default=""),
    folder_id: str = Form(default=""),
    credentials: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("storage")),
    _csrf: None = Depends(require_csrf),
):
    row = db.execute(select(StorageSettings).limit(1)).scalar_one_or_none()
    if row is None:
        row = StorageSettings()
        db.add(row)

    row.enabled = bool(enabled)
    row.folder_id = folder_id.strip() or None
    row.updated_at = utcnow()

    if credentials is not None and credentials.filename:
        raw = await credentials.read()
        if len(raw) > 64 * 1024:
            return _redirect("/admin/storage", "Berkas JSON terlalu besar.", "err")
        try:
            info = json.loads(raw)
        except ValueError:
            return _redirect(
                "/admin/storage", "Berkas bukan JSON yang valid.", "err"
            )
        email = (info.get("client_email") or "").strip()
        if not email or not info.get("private_key"):
            return _redirect(
                "/admin/storage",
                "JSON bukan service account Google (client_email/private_key "
                "tidak ditemukan).",
                "err",
            )
        store_credentials(db, row, raw.decode("utf-8"), email)

    append_event(
        db,
        "storage.saved",
        {
            "enabled": row.enabled,
            "folder_id": row.folder_id,
            "account": row.account_email,
        },
    )
    db.commit()
    return _redirect("/admin/storage", "Pengaturan penyimpanan disimpan.")


@router.post("/storage/test")
def storage_test(
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("storage")),
    _csrf: None = Depends(require_csrf),
):
    config = get_drive_config(db)
    if not config.ready:
        return _redirect(
            "/admin/storage",
            "Lengkapi kredensial dan Folder ID terlebih dahulu.",
            "err",
        )
    try:
        name = drive_test_connection(config)
    except DriveError as exc:
        return _redirect("/admin/storage", str(exc), "err")
    return _redirect(
        "/admin/storage", f"Terhubung ke folder Drive: {name}."
    )


@router.post("/storage/folders")
def storage_folder_add(
    name: str = Form(default=""),
    folder_id: str = Form(default=""),
    is_default: str = Form(default=""),
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("storage")),
    _csrf: None = Depends(require_csrf),
):
    name = name.strip()
    folder_id = folder_id.strip()
    if not name or not folder_id:
        return _redirect(
            "/admin/storage", "Nama folder dan Folder ID wajib diisi.", "err"
        )
    existing = db.execute(
        select(DriveFolder).where(DriveFolder.folder_id == folder_id).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return _redirect(
            "/admin/storage", "Folder ID tersebut sudah terdaftar.", "err"
        )

    row = DriveFolder(name=name, folder_id=folder_id, is_default=bool(is_default))
    if bool(is_default):
        for other in _drive_folders(db):
            other.is_default = False
    db.add(row)
    append_event(
        db,
        "drive.folder_added",
        {"name": name, "folder_id": folder_id, "default": row.is_default},
    )
    db.commit()
    return _redirect("/admin/storage", f"Folder '{name}' ditambahkan.")


@router.post("/storage/folders/{ref}/delete")
def storage_folder_delete(
    ref: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("storage")),
    _csrf: None = Depends(require_csrf),
):
    row = db.get(DriveFolder, ref)
    if row is None:
        return _redirect("/admin/storage", "Folder tidak ditemukan.", "err")
    for template in db.execute(
        select(LetterTemplate).where(LetterTemplate.drive_folder_ref == row.id)
    ).scalars():
        template.drive_folder_ref = None
    name = row.name
    append_event(db, "drive.folder_deleted", {"name": name})
    db.delete(row)
    db.commit()
    return _redirect(
        "/admin/storage",
        f"Folder '{name}' dihapus. Format surat memakai folder default.",
    )


@router.post("/storage/folders/{ref}/default")
def storage_folder_default(
    ref: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_permission("storage")),
    _csrf: None = Depends(require_csrf),
):
    row = db.get(DriveFolder, ref)
    if row is None:
        return _redirect("/admin/storage", "Folder tidak ditemukan.", "err")
    for other in _drive_folders(db):
        other.is_default = other.id == row.id
    db.commit()
    return _redirect(
        "/admin/storage", f"Folder '{row.name}' dijadikan default."
    )


@router.get("/permissions", response_class=HTMLResponse)
def permissions_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
):
    current = {
        role: load_permissions(db, role) for role in CONFIGURABLE_ROLES
    }
    return templates.TemplateResponse(
        request,
        "admin/permissions.html",
        _context(
            request,
            active="permissions",
            features=FEATURES,
            roles=CONFIGURABLE_ROLES,
            role_labels=ROLE_LABELS,
            current_permissions=current,
        ),
    )


@router.post("/permissions")
async def permissions_save(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
    _super=Depends(require_superuser),
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    valid = {key for key, _ in FEATURES}
    for role in CONFIGURABLE_ROLES:
        selected = {
            value
            for key, value in form.multi_items()
            if key == f"perms_{role}" and value in valid
        }
        row = db.get(RolePermission, role)
        if row is None:
            row = RolePermission(role=role)
            db.add(row)
        row.permissions = sorted(selected)
        row.updated_at = utcnow()
    append_event(
        db,
        "permissions.saved",
        {"roles": list(CONFIGURABLE_ROLES)},
    )
    db.commit()
    return _redirect("/admin/permissions", "Hak akses disimpan.")
