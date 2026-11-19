from __future__ import annotations

import re
import uuid
from pathlib import Path

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


def _login(client, username: str, password: str) -> None:
    response = client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf": _csrf()},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _create_master(admin_client) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:6]
    admin_client.post(
        "/admin/master/institution",
        data={
            "csrf": _csrf(),
            "name": f"Lembaga Uji {suffix}",
            "code": f"L-{suffix}",
            "head_name": "Ketua Lembaga",
            "head_title": "Ketua",
            "address": "Cirebon",
        },
        follow_redirects=False,
    )
    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import Faculty, Institution, StudyProgram

    db = get_session_factory()()
    institution = db.execute(
        select(Institution).where(Institution.code == f"L-{suffix}")
    ).scalar_one()

    admin_client.post(
        "/admin/master/faculty",
        data={
            "csrf": _csrf(),
            "institution_id": institution.id,
            "name": f"Fakultas Uji {suffix}",
            "code": f"F-{suffix}",
            "head_name": "Dekan Uji",
            "head_title": "Dekan",
        },
        follow_redirects=False,
    )
    faculty = db.execute(
        select(Faculty).where(Faculty.code == f"F-{suffix}")
    ).scalar_one()

    admin_client.post(
        "/admin/master/program",
        data={
            "csrf": _csrf(),
            "faculty_id": faculty.id,
            "name": f"Prodi Uji {suffix}",
            "code": f"P-{suffix}",
            "level": "S1",
            "head_name": "Kaprodi Uji",
            "head_title": "Ketua Program Studi",
        },
        follow_redirects=False,
    )
    program = db.execute(
        select(StudyProgram).where(StudyProgram.code == f"P-{suffix}")
    ).scalar_one()
    db.close()
    return institution.id, faculty.id, program.id


def test_master_data_and_public_letter_flow(admin_client, sample_pdf):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    institution_id, faculty_id, program_id = _create_master(admin_client)

    master_page = admin_client.get("/admin/master?tab=programs")
    assert master_page.status_code == 200
    assert "Daftar program studi" in master_page.text
    assert ">Edit<" in master_page.text
    assert ">Hapus<" in master_page.text

    form = admin_client.get("/permohonan")
    assert form.status_code == 200
    assert "Ajukan Surat Akademik" in form.text

    nim = f"NIM-{uuid.uuid4().hex[:10]}"
    submit = admin_client.post(
        "/permohonan",
        data={
            "csrf": _csrf(),
            "applicant_name": "Pemohon Uji",
            "nim": nim,
            "birth_place": "Cirebon",
            "birth_date": "2000-01-01",
            "email": "pemohon@example.test",
            "phone": "08123456789",
            "address": "Cirebon",
            "institution_id": institution_id,
            "faculty_id": faculty_id,
            "program_id": program_id,
            "semester": "4",
            "academic_year": "2025/2026 Ganjil",
            "letter_type": "Surat Keterangan Aktif Kuliah",
            "purpose": "Beasiswa",
            "notes": "Uji alur",
            "research_title": "",
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303, submit.text
    request_no = re.search(r"REQ-[A-Z0-9-]+", submit.headers["location"]).group(0)

    tracking = admin_client.get("/permohonan/status", params={"q": nim})
    assert tracking.status_code == 200
    assert request_no in tracking.text
    assert "Diajukan" in tracking.text

    listing = admin_client.get("/admin/letters")
    assert listing.status_code == 200
    assert request_no in listing.text
    letter_id = re.search(r"/admin/letters/([0-9a-f-]{36})", listing.text).group(1)

    verify = admin_client.post(
        f"/admin/letters/{letter_id}/verify",
        data={"csrf": _csrf(), "next": f"/admin/letters/{letter_id}"},
        follow_redirects=False,
    )
    assert verify.status_code == 303

    letter_no = f"B-{uuid.uuid4().hex[:6]}/Un.05/Ps/2026"
    approve = admin_client.post(
        f"/admin/letters/{letter_id}/approve",
        data={
            "csrf": _csrf(),
            "letter_no": letter_no,
            "next": f"/admin/letters/{letter_id}",
        },
        follow_redirects=False,
    )
    assert approve.status_code == 303

    detail = admin_client.get(f"/admin/letters/{letter_id}")
    assert detail.status_code == 200
    assert letter_no in detail.text

    upload = admin_client.post(
        f"/admin/letters/{letter_id}/document",
        data={"csrf": _csrf()},
        files={"file": ("surat-final.pdf", sample_pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    assert "/admin/documents/" in upload.headers["location"]

    detail = admin_client.get(f"/admin/letters/{letter_id}")
    assert "Dokumen terhubung" in detail.text

    validation = admin_client.get(
        "/permohonan/validasi", params={"no": letter_no}
    )
    assert validation.status_code == 200
    assert "Surat terverifikasi" in validation.text
    assert nim in validation.text


def test_multi_user_and_role_restriction(admin_client):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    username = f"operator_{uuid.uuid4().hex[:6]}"
    password = "operator-pass-123"
    created = admin_client.post(
        "/admin/users",
        data={
            "csrf": _csrf(),
            "name": "Operator Uji",
            "username": username,
            "email": "operator@example.test",
            "role": "operator",
            "password": password,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    users_page = admin_client.get("/admin/users")
    assert users_page.status_code == 200
    assert username in users_page.text
    assert "Edit data pengguna" in users_page.text
    assert "Hapus pengguna ini?" in users_page.text

    logout = admin_client.post(
        "/admin/logout", data={"csrf": _csrf()}, follow_redirects=False
    )
    assert logout.status_code == 303

    _login(admin_client, username, password)
    assert admin_client.get("/admin/letters").status_code == 200
    assert admin_client.get("/admin/users").status_code == 403
    denied = admin_client.get("/admin/master", follow_redirects=False)
    assert denied.status_code == 303
    assert "/admin?" in denied.headers["location"]
    dashboard = admin_client.get("/admin")
    assert dashboard.status_code == 200
    menu = dashboard.text.split("Portal Publik")[0]
    assert "Pengguna" not in menu
    assert "Lembaga &amp; Prodi" not in menu


def test_public_letter_pages_render(admin_client):
    for path, marker in (
        ("/permohonan", "Ajukan Surat Akademik"),
        ("/permohonan/status", "Lacak Permohonan Surat"),
        ("/permohonan/validasi", "Validasi Surat Resmi"),
    ):
        response = admin_client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text


def test_public_pages_follow_visibility_rules(admin_client, sample_pdf):
    login = admin_client.get("/admin/login")
    assert login.status_code == 200
    assert "TTE_ADMIN_TOKEN" not in login.text
    assert "Tidak Tersertifikasi" not in login.text

    for path in ("/", "/permohonan", "/permohonan/status", "/permohonan/validasi"):
        page = admin_client.get(path)
        assert page.status_code == 200
        assert "Hak cipta dilindungi" in page.text
        assert "Pusat Data" in page.text
        assert "Tidak Tersertifikasi" not in page.text
        assert "non-PSrE" not in page.text
        assert 'href="/ca"' not in page.text
        assert 'href="/crl/stit.crl"' not in page.text
        assert 'href="/docs"' not in page.text

    validation = admin_client.get("/permohonan/validasi")
    assert "Periksa PDF bertanda tangan" in validation.text

    check = admin_client.post(
        "/permohonan/validasi/dokumen",
        data={"csrf": _csrf()},
        files={"file": ("dokumen.pdf", sample_pdf, "application/pdf")},
    )
    assert check.status_code == 200
    assert "Hasil pemeriksaan" in check.text
    assert "UNSIGNED" in check.text

    # Halaman CA tetap butuh login; berkas sertifikat & CRL dibuka publik
    # agar pihak luar dapat memverifikasi tanda tangan.
    assert admin_client.get("/ca", follow_redirects=False).status_code == 303
    assert admin_client.get("/ca/root.crt").status_code == 200
    assert admin_client.get("/ca/intermediate.crt").status_code == 200
    assert admin_client.get("/crl/stit.crl").status_code == 200
    assert admin_client.get("/docs", follow_redirects=False).status_code == 303


def test_admin_logout_button_visible(admin_client):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    page = admin_client.get("/admin")
    assert page.status_code == 200
    assert "Keluar" in page.text
    assert 'action="/admin/logout"' in page.text
    assert "TTE_ADMIN_TOKEN masih default" not in page.text
    assert "Hak cipta dilindungi" in page.text
    assert "Dibuat oleh <strong>Pusat Data</strong>" in page.text
    assert "STIT Buntet Pesantren Cirebon" in page.text


def test_flow_number_steps_render(admin_client):
    page = admin_client.get("/")
    assert page.status_code == 200
    for number in ("1", "2", "3", "4"):
        assert f'<span class="step-num">{number}</span>' in page.text


def test_user_edit_and_delete(admin_client):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    username = f"crud_{uuid.uuid4().hex[:6]}"
    admin_client.post(
        "/admin/users",
        data={
            "csrf": _csrf(),
            "name": "User CRUD",
            "username": username,
            "email": "crud@example.test",
            "role": "operator",
            "password": "crud-pass-123",
        },
        follow_redirects=False,
    )
    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import User

    db = get_session_factory()()
    user = db.execute(select(User).where(User.username == username)).scalar_one()
    user_id = user.id

    edited = admin_client.post(
        f"/admin/users/{user_id}/edit",
        data={
            "csrf": _csrf(),
            "name": "User CRUD Diubah",
            "username": username,
            "email": "baru@example.test",
            "role": "operator",
        },
        follow_redirects=False,
    )
    assert edited.status_code == 303
    db.expire_all()
    user = db.get(User, user_id)
    assert user.name == "User CRUD Diubah"
    assert user.email == "baru@example.test"

    deleted = admin_client.post(
        f"/admin/users/{user_id}/delete",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    db.expire_all()
    assert db.get(User, user_id) is None
    db.close()


def test_signer_edit_and_delete(admin_client):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    nip = f"NIP-{uuid.uuid4().hex[:10]}"
    admin_client.post(
        "/admin/signers",
        data={
            "csrf": _csrf(),
            "name": "Penanda CRUD",
            "jabatan": "Rektor",
            "nip": nip,
            "email": "ttd@example.test",
        },
        follow_redirects=False,
    )
    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import SignerProfile

    db = get_session_factory()()
    signer = db.execute(
        select(SignerProfile).where(SignerProfile.nip == nip)
    ).scalar_one()
    signer_id = signer.id
    assert signer.p12_path, "sertifikat signer harus diterbitkan otomatis"
    certificate_path = Path(signer.p12_path)
    certificate_dir = certificate_path.parent
    assert certificate_path.exists()
    assert signer.cert_subject
    assert "Penanda CRUD" in signer.cert_subject

    signers_page = admin_client.get("/admin/signers")
    assert signers_page.status_code == 200
    assert "Edit penanda tangan" in signers_page.text
    assert "Hapus penanda tangan ini?" in signers_page.text

    edited = admin_client.post(
        f"/admin/signers/{signer_id}/edit",
        data={
            "csrf": _csrf(),
            "name": "Penanda CRUD Diubah",
            "jabatan": "Wakil Rektor",
            "nip": nip,
            "email": "baru-ttd@example.test",
        },
        follow_redirects=False,
    )
    assert edited.status_code == 303
    db.expire_all()
    signer = db.get(SignerProfile, signer_id)
    assert signer.name == "Penanda CRUD Diubah"
    assert signer.jabatan == "Wakil Rektor"

    deleted = admin_client.post(
        f"/admin/signers/{signer_id}/delete",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    db.expire_all()
    assert db.get(SignerProfile, signer_id) is None
    assert not certificate_dir.exists(), "folder sertifikat harus dihapus"
    db.close()


def test_document_delete(admin_client, sample_pdf):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    upload = admin_client.post(
        "/admin/documents",
        data={"csrf": _csrf()},
        files={"file": ("hapus-uji.pdf", sample_pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    document_id = upload.headers["location"].split("/admin/documents/")[1].split("?")[0]

    from pathlib import Path

    from tte_core.db import get_session_factory
    from tte_core.models import Document

    db = get_session_factory()()
    doc = db.get(Document, document_id)
    short_id = doc.short_id
    folder = Path(doc.original_path).parent
    assert folder.exists()

    deleted = admin_client.post(
        f"/admin/documents/{document_id}/delete",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    db.expire_all()
    assert db.get(Document, document_id) is None
    assert not folder.exists()
    db.close()

    listing = admin_client.get("/admin/documents")
    assert short_id not in listing.text


def test_permissions_page_and_grant(admin_client):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    page = admin_client.get("/admin/permissions")
    assert page.status_code == 200
    assert "Matriks izin fitur" in page.text
    assert "Operator Fakultas" in page.text
    assert "Operator Prodi" in page.text

    from sqlalchemy import select as _select

    from tte_core.db import get_session_factory as _sf
    from tte_core.models import StudyProgram as _Program

    _db = _sf()()
    program = _db.execute(_select(_Program).limit(1)).scalar_one()
    program_pk = program.id
    _db.close()

    username = f"akses_{uuid.uuid4().hex[:6]}"
    password = "akses-pass-123"
    created = admin_client.post(
        "/admin/users",
        data={
            "csrf": _csrf(),
            "name": "Operator Akses",
            "username": username,
            "email": "akses@example.test",
            "role": "operator_prodi",
            "program_id": program_pk,
            "password": password,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    admin_client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(admin_client, username, password)

    # Role operator prodi: tanpa akses master dan tanpa halaman hak akses.
    assert admin_client.get("/admin/permissions").status_code == 403
    denied = admin_client.get("/admin/master", follow_redirects=False)
    assert denied.status_code == 303
    assert "/admin?" in denied.headers["location"]

    admin_client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)

    from urllib.parse import urlencode

    granted = admin_client.post(
        "/admin/permissions",
        content=urlencode(
            [
                ("csrf", _csrf()),
                ("perms_operator_prodi", "letters"),
                ("perms_operator_prodi", "master"),
            ]
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert granted.status_code == 303

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import RolePermission

    db = get_session_factory()()
    row = db.get(RolePermission, "operator_prodi")
    assert "master" in row.permissions
    assert "letters" in row.permissions
    db.close()

    admin_client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(admin_client, username, password)

    master = admin_client.get("/admin/master")
    assert master.status_code == 200
    dashboard = admin_client.get("/admin")
    assert "Lembaga &amp; Prodi" in dashboard.text.split("Portal Publik")[0]
    assert "Dokumen" not in dashboard.text.split("Portal Publik")[0]


def test_letter_data_scoped_by_unit(admin_client):
    _login(admin_client, get_settings().admin_username, get_settings().admin_token)
    institution_id, faculty_id, program_a = _create_master(admin_client)

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import Faculty, LetterRequest, StudyProgram

    db = get_session_factory()()
    suffix = uuid.uuid4().hex[:6]
    program_b_row = StudyProgram(
        faculty_id=faculty_id,
        name=f"Prodi B {suffix}",
        code=f"PB-{suffix}",
        level="S1",
        head_title="Ketua Program Studi",
    )
    other_faculty = Faculty(
        institution_id=institution_id,
        name=f"Fakultas Lain {suffix}",
        code=f"FL-{suffix}",
        head_title="Dekan",
    )
    db.add_all([program_b_row, other_faculty])
    db.flush()
    program_c_row = StudyProgram(
        faculty_id=other_faculty.id,
        name=f"Prodi C {suffix}",
        code=f"PC-{suffix}",
        level="S1",
        head_title="Ketua Program Studi",
    )
    db.add(program_c_row)
    db.commit()
    program_b = program_b_row.id
    other_faculty_id = other_faculty.id
    program_c = program_c_row.id
    db.close()

    def submit(program: str) -> str:
        nim = f"NIM-{uuid.uuid4().hex[:10]}"
        response = admin_client.post(
            "/permohonan",
            data={
                "csrf": _csrf(),
                "applicant_name": f"Pemohon {program[:4]}",
                "nim": nim,
                "email": f"{nim.lower()}@example.test",
                "institution_id": institution_id,
                "faculty_id": faculty_id if program != program_c else other_faculty_id,
                "program_id": program,
                "letter_type": "Surat Keterangan Aktif Kuliah",
                "purpose": "Beasiswa",
                "notes": "Uji pembatasan data",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        return re.search(r"REQ-[A-Z0-9-]+", response.headers["location"]).group(0)

    req_a = submit(program_a)
    req_b = submit(program_b)
    req_c = submit(program_c)

    db = get_session_factory()()
    ids = {
        row.request_no: row.id
        for row in db.execute(
            select(LetterRequest).where(
                LetterRequest.request_no.in_([req_a, req_b, req_c])
            )
        ).scalars()
    }
    db.close()

    def make_user(role: str, faculty: str = "", program: str = "") -> tuple[str, str]:
        username = f"scope_{uuid.uuid4().hex[:6]}"
        password = "scope-pass-123"
        created = admin_client.post(
            "/admin/users",
            data={
                "csrf": _csrf(),
                "name": f"Scope {role}",
                "username": username,
                "role": role,
                "faculty_id": faculty,
                "program_id": program,
                "password": password,
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        return username, password

    users = {
        "prodi": make_user("operator_prodi", program=program_a),
        "fakultas": make_user("operator_fakultas", faculty=faculty_id),
        "tu": make_user("operator"),
    }

    def check(role_key: str, visible: list[str], hidden: list[str]) -> None:
        username, password = users[role_key]
        admin_client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
        _login(admin_client, username, password)
        page = admin_client.get("/admin/letters")
        assert page.status_code == 200
        for no in visible:
            assert no in page.text, f"{role_key} harus melihat {no}"
        for no in hidden:
            assert no not in page.text, f"{role_key} tidak boleh melihat {no}"
        # Detail permohonan di luar lingkup ditolak.
        for no in hidden:
            denied = admin_client.get(
                f"/admin/letters/{ids[no]}", follow_redirects=False
            )
            assert denied.status_code == 303
            assert "/admin/letters?" in denied.headers["location"]
        admin_client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)

    # Operator prodi hanya melihat prodinya sendiri.
    check("prodi", [req_a], [req_b, req_c])
    # Operator fakultas melihat semua prodi di fakultasnya.
    check("fakultas", [req_a, req_b], [req_c])
    # Operator TU melihat semua.
    check("tu", [req_a, req_b, req_c], [])
