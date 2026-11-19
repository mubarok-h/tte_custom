from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from tte_core.admin_auth import csrf_token
from tte_core.config import get_settings


def _csrf() -> str:
    return csrf_token(get_settings())


def _login(client) -> None:
    response = client.post(
        "/admin/login",
        data={
            "username": get_settings().admin_username,
            "password": get_settings().admin_token,
            "csrf": _csrf(),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _credentials() -> bytes:
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "layanan@contoh.iam.gserviceaccount.com",
            "private_key": (
                "-----BEGIN PRIVATE KEY-----\nRAHASIA-KUNCI\n"
                "-----END PRIVATE KEY-----\n"
            ),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ).encode("utf-8")


def _save_drive(client, enabled: bool, folder: str = "folder-uji-123") -> None:
    data = {"csrf": _csrf(), "folder_id": folder}
    if enabled:
        data["enabled"] = "1"
    response = client.post(
        "/admin/storage",
        data=data,
        files={
            "credentials": (
                "service-account.json",
                _credentials(),
                "application/json",
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


@pytest.fixture()
def drive_on(client):
    _login(client)
    _save_drive(client, True)
    yield
    _save_drive(client, False)


def test_storage_settings_saved_from_gui(client):
    _login(client)
    page = client.get("/admin/storage")
    assert page.status_code == 200
    assert "Penyimpanan Google Drive" in page.text

    _save_drive(client, True)

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import StorageSettings

    db = get_session_factory()()
    row = db.execute(select(StorageSettings)).scalar_one()
    assert row.enabled is True
    assert row.folder_id == "folder-uji-123"
    assert row.account_email == "layanan@contoh.iam.gserviceaccount.com"
    assert row.credentials_enc
    assert "RAHASIA-KUNCI" not in row.credentials_enc
    db.close()

    page = client.get("/admin/storage")
    assert "layanan@contoh.iam.gserviceaccount.com" in page.text
    assert "RAHASIA-KUNCI" not in page.text

    _save_drive(client, False)


def test_upload_document_is_mirrored_to_drive(client, sample_pdf, drive_on, monkeypatch):
    from tte_core import drive

    calls = []

    def fake_upload(config, *, name, data, mime, folder_id=None):
        calls.append((name, mime, len(data), folder_id))
        return "drive-original-1"

    monkeypatch.setattr(drive, "upload_bytes", fake_upload)

    response = client.post(
        "/api/v1/documents",
        files={"file": ("drive-uji.pdf", sample_pdf, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    doc_id = response.json()["id"]

    from tte_core.db import get_session_factory
    from tte_core.models import Document

    db = get_session_factory()()
    doc = db.get(Document, doc_id)
    assert doc.drive_original_id == "drive-original-1"
    db.close()
    assert calls and calls[0][1] == "application/pdf"
    assert calls[0][3] == "folder-uji-123"  # folder default dari pengaturan


def test_signed_pdf_is_mirrored_and_restored(client, sample_pdf, drive_on, monkeypatch):
    from tte_core import drive

    ids = {"original": "drive-orig", "signed": "drive-signed"}
    monkeypatch.setattr(
        drive,
        "upload_bytes",
        lambda config, **kwargs: ids["signed"]
        if kwargs["name"].endswith("-tte.pdf")
        else ids["original"],
    )

    upload = client.post(
        "/api/v1/documents",
        files={"file": ("drive-sign.pdf", sample_pdf, "application/pdf")},
    )
    doc_id = upload.json()["id"]

    signer_nip = f"DRV-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/sign-requests",
        json={
            "document_id": doc_id,
            "signer": {
                "name": "Penanda Drive",
                "jabatan": "Rektor",
                "nip": signer_nip,
                "email": "drive@example.test",
            },
            "reason": "Pengesahan",
        },
    )
    assert created.status_code == 200, created.text
    request_id = created.json()["id"]
    client.post(f"/api/v1/sign-requests/{request_id}/consent", json={"consent": True})
    signed = client.post(f"/api/v1/sign-requests/{request_id}/sign")
    assert signed.status_code == 200, signed.text

    from tte_core.db import get_session_factory
    from tte_core.models import Document

    db = get_session_factory()()
    doc = db.get(Document, doc_id)
    assert doc.drive_signed_id == "drive-signed"
    assert doc.drive_original_id == "drive-orig"

    # Hapus salinan lokal: unduhan harus dipulihkan dari Drive.
    local = Path(doc.signed_path)
    signed_bytes = local.read_bytes()
    local.unlink()
    assert not local.exists()
    db.close()

    monkeypatch.setattr(
        drive, "download_bytes", lambda config, file_id: signed_bytes
    )
    restored = client.get(
        f"/admin/documents/{doc_id}/file", params={"variant": "signed"}
    )
    assert restored.status_code == 200
    assert restored.content.startswith(b"%PDF")
    assert local.exists()


def test_delete_document_removes_drive_files(client, sample_pdf, drive_on, monkeypatch):
    from tte_core import drive

    monkeypatch.setattr(drive, "upload_bytes", lambda config, **kwargs: "drive-x")

    upload = client.post(
        "/api/v1/documents",
        files={"file": ("drive-del.pdf", sample_pdf, "application/pdf")},
    )
    doc_id = upload.json()["id"]

    removed = []
    # admin.py mengimpor fungsi ini langsung, jadi patch pada modul admin.
    monkeypatch.setattr(
        "tte_core.api.admin.drive_delete_file",
        lambda config, file_id: removed.append(file_id),
    )

    response = client.post(
        f"/admin/documents/{doc_id}/delete",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "drive-x" in removed


def test_drive_folders_per_letter_template(client, drive_on):
    _login(client)
    added = client.post(
        "/admin/storage/folders",
        data={
            "csrf": _csrf(),
            "name": "Surat Keterangan",
            "folder_id": "folder-keterangan-999",
            "is_default": "",
        },
        follow_redirects=False,
    )
    assert added.status_code == 303

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import DriveFolder, LetterTemplate

    db = get_session_factory()()
    folder = db.execute(
        select(DriveFolder).where(DriveFolder.folder_id == "folder-keterangan-999")
    ).scalar_one()

    letter_type = f"Surat Folder {uuid.uuid4().hex[:6]}"
    saved = client.post(
        "/admin/letter-templates/save",
        data={
            "csrf": _csrf(),
            "letter_type": letter_type,
            "name": "Format folder",
            "body_html": "<p>[Nama]</p>",
            "signer_id": "",
            "drive_folder_ref": folder.id,
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303

    template = db.execute(
        select(LetterTemplate).where(LetterTemplate.letter_type == letter_type)
    ).scalar_one()
    assert template.drive_folder_ref == folder.id

    from tte_core.drive import folder_for_ref

    assert folder_for_ref(db, template.drive_folder_ref) == "folder-keterangan-999"
    folder_pk = folder.id

    # Hapus folder: referensi template harus dikosongkan.
    removed = client.post(
        f"/admin/storage/folders/{folder_pk}/delete",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert removed.status_code == 303
    db.expire_all()
    template = db.execute(
        select(LetterTemplate).where(LetterTemplate.letter_type == letter_type)
    ).scalar_one()
    assert template.drive_folder_ref is None
    remaining = db.execute(
        select(DriveFolder).where(DriveFolder.id == folder_pk)
    ).scalar_one_or_none()
    assert remaining is None
    db.close()
