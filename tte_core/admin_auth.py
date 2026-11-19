from __future__ import annotations

"""Autentikasi panel admin berbasis akun pengguna + cookie bertanda tangan.

Password disimpan sebagai hash PBKDF2-SHA256. Cookie sesi hanya berisi id
pengguna, username, role, dan waktu kedaluwarsa, ditandatangani HMAC-SHA256
dengan `TTE_TOKEN_SECRET`. Semua form POST wajib menyertakan token CSRF.
"""

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import RolePermission, User

ADMIN_COOKIE = "tte_admin_session"
_CSRF_CONTEXT = b"tte-admin-csrf-v1"

# Fitur yang dapat diatur hak aksesnya (admin selalu punya semua).
FEATURES: tuple[tuple[str, str], ...] = (
    ("letters", "Permintaan Surat"),
    ("documents", "Dokumen"),
    ("requests", "Tanda Tangan"),
    ("templates", "Format Surat & Kop"),
    ("master", "Lembaga & Prodi"),
    ("signers", "Penanda Tangan"),
    ("users", "Pengguna"),
    ("audit", "Audit Log"),
    ("email", "Email"),
    ("storage", "Penyimpanan"),
    ("ca", "Sertifikat CA"),
)
ALL_PERMISSIONS = frozenset(key for key, _ in FEATURES)

ROLE_LABELS: dict[str, str] = {
    "admin": "Administrator",
    "operator": "Operator TU",
    "operator_fakultas": "Operator Fakultas",
    "operator_prodi": "Operator Prodi",
}

# Role selain admin yang dapat diatur.
CONFIGURABLE_ROLES = tuple(r for r in ROLE_LABELS if r != "admin")

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "operator": {"letters", "documents", "requests"},
    "operator_fakultas": {"letters", "documents", "requests"},
    "operator_prodi": {"letters", "documents"},
}


@dataclass(frozen=True)
class AdminIdentity:
    id: str
    username: str
    name: str
    role: str
    permissions: frozenset[str] = frozenset()
    faculty_id: str | None = None
    program_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    def can(self, feature: str) -> bool:
        return self.role == "admin" or feature in self.permissions


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def create_session(
    settings: Settings,
    user: User,
    *,
    now: float | None = None,
) -> str:
    issued = int(now if now is not None else time.time())
    expires = issued + int(settings.admin_session_hours * 3600)
    payload = _b64encode(
        json.dumps(
            {
                "sub": user.id,
                "usr": user.username,
                "role": user.role,
                "iat": issued,
                "exp": expires,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{payload}.{_sign(payload, settings.token_secret)}"


def decode_session(token: str | None, settings: Settings) -> dict | None:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = _sign(payload, settings.token_secret)
    if not hmac.compare_digest(
        signature.encode("utf-8"), expected.encode("utf-8")
    ):
        return None
    try:
        data = json.loads(_b64decode(payload))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        expires = int(data.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if expires <= int(time.time()):
        return None
    if not data.get("sub"):
        return None
    return data


def verify_session(token: str | None, settings: Settings) -> bool:
    return decode_session(token, settings) is not None


def is_authenticated(request: Request) -> bool:
    return verify_session(
        request.cookies.get(ADMIN_COOKIE), get_settings()
    )


def csrf_token(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return hmac.new(
        settings.token_secret.encode("utf-8"),
        _CSRF_CONTEXT,
        hashlib.sha256,
    ).hexdigest()[:40]


def _login_redirect(request: Request) -> HTTPException:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/admin/login?next={quote(target, safe='/')}"},
    )


def load_permissions(db: Session, role: str) -> frozenset[str]:
    if role == "admin":
        return ALL_PERMISSIONS
    row = db.get(RolePermission, role)
    if row is None or not row.permissions:
        return frozenset(DEFAULT_ROLE_PERMISSIONS.get(role, set()))
    return frozenset(row.permissions)


def require_permission(feature: str):
    """Dependency factory: pastikan user punya akses ke fitur tertentu."""

    def dependency(
        request: Request,
        user: AdminIdentity = Depends(require_admin),
    ) -> AdminIdentity:
        if not user.can(feature):
            label = dict(FEATURES).get(feature, feature)
            message = quote(f"Anda tidak memiliki akses ke {label}.")
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={
                    "Location": f"/admin?msg={message}&kind=err"
                },
            )
        return user

    return dependency


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> AdminIdentity:
    """Dependency: validasi sesi dan status akun pengguna."""
    data = decode_session(
        request.cookies.get(ADMIN_COOKIE), get_settings()
    )
    if data is None:
        raise _login_redirect(request)

    user = db.get(User, data["sub"])
    if user is None or not user.is_active:
        raise _login_redirect(request)

    identity = AdminIdentity(
        id=user.id,
        username=user.username,
        name=user.name,
        role=user.role,
        permissions=load_permissions(db, user.role),
        faculty_id=user.faculty_id,
        program_id=user.program_id,
    )
    request.state.user = identity
    return identity


def require_superuser(request: Request) -> AdminIdentity:
    user = getattr(request.state, "user", None)
    if user is None:
        raise _login_redirect(request)
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Halaman ini hanya dapat diakses Administrator.",
        )
    return user


def require_csrf(csrf: str = Form(default="")) -> None:
    expected = csrf_token()
    if not csrf or not hmac.compare_digest(
        csrf.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=400, detail="Token CSRF tidak valid.")


_WEAK_ADMIN_TOKENS = {
    "",
    "dev-admin-token",
    "ganti-token-admin",
    "token-lokal",
    "changeme",
}


def weak_credentials(settings: Settings) -> list[str]:
    warnings: list[str] = []
    if settings.token_secret in {"", "dev-secret-change-me"}:
        warnings.append(
            "TTE_TOKEN_SECRET masih default; cookie admin dan tautan verifikasi "
            "tidak aman. Ganti dengan rahasia acak yang panjang."
        )
    return warnings
