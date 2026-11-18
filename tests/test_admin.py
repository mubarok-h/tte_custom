from __future__ import annotations

import re

import pytest

from tte_core.admin_auth import csrf_token
from tte_core.config import get_settings


@pytest.fixture()
def admin_client():
    from fastapi.testclient import TestClient

    from tte_core.app import app

    with TestClient(app) as client:
        yield client


def _csrf() -> str:
    return csrf_token(get_settings())


def _login(client) -> None:
    settings = get_settings()
    response = client.post(
        "/admin/login",
        data={
            "username": settings.admin_username,
            "password": settings.admin_token,
            "csrf": _csrf(),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def test_admin_requires_login(admin_client):
    response = admin_client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")


def test_admin_login_rejects_wrong_password(admin_client):
    response = admin_client.post(
        "/admin/login",
        data={"username": "admin", "password": "salah", "csrf": _csrf()},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "salah" in response.text


def test_admin_login_and_pages(admin_client):
    _login(admin_client)

    for path, marker in [
        ("/admin", "Dashboard"),
        ("/admin/documents", "Unggah PDF baru"),
        ("/admin/signers", "Tambah penanda tangan"),
        ("/admin/requests", "Permintaan Tanda Tangan"),
        ("/admin/audit", "Audit Log"),
    ]:
        response = admin_client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert marker in response.text

    again = admin_client.get("/admin/login", follow_redirects=False)
    assert again.status_code == 303


def test_admin_uses_local_sneat_assets(admin_client):
    for path in (
        "/static/sneat/css/core.css",
        "/static/sneat/css/admin.css",
        "/static/sneat/css/siap.css",
        "/static/sneat/js/main.js",
        "/static/sneat/fonts/boxicons.woff2",
        "/static/sneat/fonts/public-sans.woff2",
        "/static/sneat/fonts/plus-jakarta-sans.woff2",
        "/static/brand/stit-logo.png",
    ):
        response = admin_client.get(path)
        assert response.status_code == 200, path
        assert response.content, path

    page = admin_client.get("/admin/login")
    assert page.status_code == 200
    assert "/static/sneat/css/core.css" in page.text
    assert "cdn" not in page.text.lower()


def test_admin_csrf_required(admin_client):
    _login(admin_client)
    response = admin_client.post(
        "/admin/signers",
        data={"name": "Tanpa CSRF", "jabatan": "X", "nip": "000"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_admin_full_sign_flow(admin_client, sample_pdf):
    _login(admin_client)
    nip = "ADM-2026-001"

    upload = admin_client.post(
        "/admin/documents",
        data={"csrf": _csrf()},
        files={"file": ("admin-uji.pdf", sample_pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert upload.status_code == 303, upload.text
    location = upload.headers["location"]
    document_id = location.split("/admin/documents/")[1].split("?")[0]

    signer = admin_client.post(
        "/admin/signers",
        data={
            "csrf": _csrf(),
            "name": "Rektor Uji Admin",
            "jabatan": "Rektor",
            "nip": nip,
            "email": "rektor-uji@example.test",
        },
        follow_redirects=False,
    )
    assert signer.status_code == 303

    detail = admin_client.get(f"/admin/documents/{document_id}")
    assert detail.status_code == 200
    signer_id = re.search(
        r'<option value="([0-9a-f-]{36})">Rektor Uji Admin', detail.text
    ).group(1)

    created = admin_client.post(
        f"/admin/documents/{document_id}/requests",
        data={
            "csrf": _csrf(),
            "signer_id": signer_id,
            "reason": "Uji panel admin",
            "location": "Buntet",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    detail = admin_client.get(f"/admin/documents/{document_id}")
    request_id = re.search(r"/admin/requests/([0-9a-f-]{36})/consent", detail.text).group(1)

    consent = admin_client.post(
        f"/admin/requests/{request_id}/consent",
        data={"csrf": _csrf(), "next": f"/admin/documents/{document_id}"},
        follow_redirects=False,
    )
    assert consent.status_code == 303

    signed = admin_client.post(
        f"/admin/requests/{request_id}/sign",
        data={"csrf": _csrf(), "next": "/admin/requests"},
        follow_redirects=False,
    )
    assert signed.status_code == 303, signed.text
    assert f"/admin/documents/{document_id}" in signed.headers["location"]

    detail = admin_client.get(f"/admin/documents/{document_id}")
    assert "signed" in detail.text
    assert "Unduh PDF TTE" in detail.text

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import SignatureRecord, SignerProfile

    db = get_session_factory()()
    signer_row = db.execute(
        select(SignerProfile).where(SignerProfile.nip == nip)
    ).scalar_one()
    signature_row = db.execute(
        select(SignatureRecord).where(
            SignatureRecord.document_id == document_id
        )
    ).scalar_one()
    assert signer_row.cert_serial
    assert signer_row.cert_subject and "Rektor Uji Admin" in signer_row.cert_subject
    assert signature_row.cert_serial == signer_row.cert_serial
    db.close()

    match = re.search(r"/v/([A-Z0-9]+)\?t=([0-9a-f]+)", detail.text)
    assert match, "tautan verifikasi tidak ditemukan"
    short_id, token = match.groups()
    portal = admin_client.get(f"/v/{short_id}", params={"t": token})
    assert portal.status_code == 200
    assert "VALID" in portal.text
    # Profil penanda tangan asli ditampilkan, bukan hanya subjek sertifikat.
    assert "Rektor Uji Admin" in portal.text

    audit = admin_client.get("/admin/audit")
    assert audit.status_code == 200
    assert "document.signed" in audit.text
    assert "Rantai audit <strong>valid</strong>" in audit.text or "valid" in audit.text

    # logout menghapus sesi
    logout = admin_client.post(
        "/admin/logout", data={"csrf": _csrf()}, follow_redirects=False
    )
    assert logout.status_code == 303
    protected = admin_client.get("/admin", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"].startswith("/admin/login")


def test_admin_verify_quick_check(admin_client, signed_document):
    _login(admin_client)
    token = signed_document["token"]
    response = admin_client.get(
        "/admin/verify",
        params={"short_id": signed_document["short_id"], "t": token},
    )
    assert response.status_code == 200
    assert "VALID" in response.text
