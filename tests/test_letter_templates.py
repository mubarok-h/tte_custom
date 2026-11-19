from __future__ import annotations

import re
import uuid

import pytest

from tte_core.admin_auth import csrf_token
from tte_core.config import get_settings
from tte_core.letter_docs import render_text


@pytest.fixture()
def admin_client():
    from fastapi.testclient import TestClient

    from tte_core.app import app

    with TestClient(app) as client:
        yield client


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


def _create_master(admin_client) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:6]
    admin_client.post(
        "/admin/master/institution",
        data={
            "csrf": _csrf(),
            "name": f"Lembaga Format {suffix}",
            "code": f"LF-{suffix}",
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
        select(Institution).where(Institution.code == f"LF-{suffix}")
    ).scalar_one()

    admin_client.post(
        "/admin/master/faculty",
        data={
            "csrf": _csrf(),
            "institution_id": institution.id,
            "name": f"Fakultas Format {suffix}",
            "code": f"FF-{suffix}",
            "head_name": "Dekan Uji",
            "head_title": "Dekan",
        },
        follow_redirects=False,
    )
    faculty = db.execute(
        select(Faculty).where(Faculty.code == f"FF-{suffix}")
    ).scalar_one()

    admin_client.post(
        "/admin/master/program",
        data={
            "csrf": _csrf(),
            "faculty_id": faculty.id,
            "name": f"Prodi Format {suffix}",
            "code": f"PF-{suffix}",
            "level": "S1",
            "head_name": "Kaprodi Uji",
            "head_title": "Ketua Program Studi",
        },
        follow_redirects=False,
    )
    program = db.execute(
        select(StudyProgram).where(StudyProgram.code == f"PF-{suffix}")
    ).scalar_one()
    db.close()
    return institution.id, faculty.id, program.id


def _submit_letter(admin_client, institution_id, faculty_id, program_id, letter_type):
    nim = f"NIM-{uuid.uuid4().hex[:10]}"
    response = admin_client.post(
        "/permohonan",
        data={
            "csrf": _csrf(),
            "applicant_name": "Pemohon Format",
            "nim": nim,
            "birth_place": "Cirebon",
            "birth_date": "2000-02-03",
            "email": "pemohon-format@example.test",
            "phone": "081200000000",
            "address": "Buntet Pesantren",
            "institution_id": institution_id,
            "faculty_id": faculty_id,
            "program_id": program_id,
            "semester": "4",
            "academic_year": "2026/2027 Ganjil",
            "letter_type": letter_type,
            "purpose": "Beasiswa",
            "notes": "Pengajuan beasiswa prestasi",
            "research_title": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    request_no = re.search(r"REQ-[A-Z0-9-]+", response.headers["location"]).group(0)
    return request_no


def test_keyword_case_rules():
    context = {
        "nama": "M. Mubarok",
        "prodi": "Manajemen Pendidikan Islam",
        "tanggal_lahir": "12 Mei 2004",
    }

    rendered, unknown = render_text(
        "[NAMA] / [nama] / [Nama] / [nAmA]", context
    )
    assert rendered == (
        "M. MUBAROK / m. mubarok / M. Mubarok / M. Mubarok"
    )
    assert unknown == []

    rendered, _ = render_text("[Program Studi]", context)
    assert rendered == "Manajemen Pendidikan Islam"

    # Singkatan pendek dipertahankan pada Title Case.
    rendered, _ = render_text("[Lembaga]", {"lembaga": "STIT Buntet Pesantren"})
    assert rendered == "STIT Buntet Pesantren"

    rendered, unknown = render_text("[tidak_ada]", context)
    assert rendered == "[tidak_ada]"
    assert unknown == ["tidak_ada"]


def test_letter_template_crud_and_public_dropdown(admin_client):
    _login(admin_client)
    letter_type = f"Surat Uji {uuid.uuid4().hex[:6]}"

    listing = admin_client.get("/admin/letter-templates")
    assert listing.status_code == 200
    assert "Format Surat" in listing.text

    head = admin_client.get("/admin/letterhead")
    assert head.status_code == 200
    assert "Kop Surat" in head.text

    logo = admin_client.get("/admin/letterhead/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/")

    head_preview = admin_client.get("/admin/letterhead/preview")
    assert head_preview.status_code == 200
    assert "/admin/letterhead/logo" in head_preview.text

    created = admin_client.post(
        "/admin/letter-templates/save",
        data={
            "csrf": _csrf(),
            "letter_type": letter_type,
            "name": "Format uji otomatis",
            "body_html": "<p>Halo [Nama], NIM [NIM].</p>[blok_ttd]",
            "signer_id": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import LetterTemplate

    db = get_session_factory()()
    template = db.execute(
        select(LetterTemplate).where(LetterTemplate.letter_type == letter_type)
    ).scalar_one()
    template_id = template.id
    db.close()

    public_form = admin_client.get("/permohonan")
    assert letter_type in public_form.text

    detail = admin_client.get(f"/admin/letter-templates/{template_id}")
    assert detail.status_code == 200
    assert "Format uji otomatis" in detail.text

    toggled = admin_client.post(
        f"/admin/letter-templates/{template_id}/toggle",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert toggled.status_code == 303
    assert letter_type not in admin_client.get("/permohonan").text

    admin_client.post(
        f"/admin/letter-templates/{template_id}/toggle",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert letter_type in admin_client.get("/permohonan").text

    deleted = admin_client.post(
        f"/admin/letter-templates/{template_id}/delete",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert letter_type not in admin_client.get("/permohonan").text


def test_generate_letter_pdf_from_template(admin_client):
    _login(admin_client)
    institution_id, faculty_id, program_id = _create_master(admin_client)

    letter_type = f"Surat Generate {uuid.uuid4().hex[:6]}"
    body = (
        "<p>Yang bertanda tangan di bawah ini menerangkan bahwa "
        "<strong>[Nama]</strong> dengan NIM [NIM] dari [Prodi] "
        "berhak mengajukan [Keperluan].</p>[blok_ttd]"
    )
    admin_client.post(
        "/admin/letter-templates/save",
        data={
            "csrf": _csrf(),
            "letter_type": letter_type,
            "name": "Format generate",
            "body_html": body,
            "signer_id": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )

    nip = f"GEN-{uuid.uuid4().hex[:8]}"
    signer = admin_client.post(
        "/admin/signers",
        data={
            "csrf": _csrf(),
            "name": "Rektor Generate",
            "jabatan": "Rektor",
            "nip": nip,
            "email": "rektor-generate@example.test",
        },
        follow_redirects=False,
    )
    assert signer.status_code == 303

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import Document, LetterRequest, SignerProfile

    db = get_session_factory()()
    signer_row = db.execute(
        select(SignerProfile).where(SignerProfile.nip == nip)
    ).scalar_one()
    signer_id = signer_row.id
    db.close()

    request_no = _submit_letter(
        admin_client, institution_id, faculty_id, program_id, letter_type
    )

    db = get_session_factory()()
    letter = db.execute(
        select(LetterRequest).where(LetterRequest.request_no == request_no)
    ).scalar_one()
    letter_id = letter.id
    assert letter.template_id is not None
    db.close()

    admin_client.post(
        f"/admin/letters/{letter_id}/verify",
        data={"csrf": _csrf(), "next": f"/admin/letters/{letter_id}"},
        follow_redirects=False,
    )
    letter_no = f"B-{uuid.uuid4().hex[:6]}/Un.05/FMT/2026"
    admin_client.post(
        f"/admin/letters/{letter_id}/approve",
        data={
            "csrf": _csrf(),
            "letter_no": letter_no,
            "next": f"/admin/letters/{letter_id}",
        },
        follow_redirects=False,
    )

    preview = admin_client.get(
        f"/admin/letters/{letter_id}/preview", params={"signer_id": signer_id}
    )
    assert preview.status_code == 200
    assert "Pemohon Format" in preview.text
    assert "Rektor Generate" in preview.text
    assert letter_no in preview.text

    generated = admin_client.post(
        f"/admin/letters/{letter_id}/generate",
        data={"csrf": _csrf(), "signer_id": signer_id},
        follow_redirects=False,
    )
    assert generated.status_code == 303, generated.text
    assert "/admin/documents/" in generated.headers["location"]
    assert f"signer={signer_id}" in generated.headers["location"]

    db = get_session_factory()()
    letter = db.get(LetterRequest, letter_id)
    assert letter.document_id is not None
    assert letter.signer_id == signer_id
    document = db.get(Document, letter.document_id)
    assert document is not None
    assert document.filename.endswith(".pdf")
    document_id = document.id
    db.close()

    download = admin_client.get(
        f"/admin/documents/{document_id}/file", params={"variant": "original"}
    )
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")


def test_unknown_keyword_blocks_generation(admin_client):
    _login(admin_client)
    institution_id, faculty_id, program_id = _create_master(admin_client)

    letter_type = f"Surat Salah Keyword {uuid.uuid4().hex[:6]}"
    admin_client.post(
        "/admin/letter-templates/save",
        data={
            "csrf": _csrf(),
            "letter_type": letter_type,
            "name": "Format salah",
            "body_html": "<p>Halo [nama_tidak_ada]</p>",
            "signer_id": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )

    nip = f"BAD-{uuid.uuid4().hex[:8]}"
    admin_client.post(
        "/admin/signers",
        data={
            "csrf": _csrf(),
            "name": "Rektor Salah",
            "jabatan": "Rektor",
            "nip": nip,
            "email": "rektor-salah@example.test",
        },
        follow_redirects=False,
    )

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import LetterRequest, SignerProfile

    db = get_session_factory()()
    signer_id = db.execute(
        select(SignerProfile.id).where(SignerProfile.nip == nip)
    ).scalar_one()
    db.close()

    request_no = _submit_letter(
        admin_client, institution_id, faculty_id, program_id, letter_type
    )

    db = get_session_factory()()
    letter_id = db.execute(
        select(LetterRequest.id).where(LetterRequest.request_no == request_no)
    ).scalar_one()
    db.close()

    admin_client.post(
        f"/admin/letters/{letter_id}/verify",
        data={"csrf": _csrf(), "next": f"/admin/letters/{letter_id}"},
        follow_redirects=False,
    )
    admin_client.post(
        f"/admin/letters/{letter_id}/approve",
        data={
            "csrf": _csrf(),
            "letter_no": f"B-{uuid.uuid4().hex[:6]}/SALAH/2026",
            "next": f"/admin/letters/{letter_id}",
        },
        follow_redirects=False,
    )

    generated = admin_client.post(
        f"/admin/letters/{letter_id}/generate",
        data={"csrf": _csrf(), "signer_id": signer_id},
        follow_redirects=True,
    )
    assert generated.status_code == 200
    assert "Keyword tidak dikenal" in generated.text

    db = get_session_factory()()
    letter = db.get(LetterRequest, letter_id)
    assert letter.document_id is None
    db.close()


def test_letterhead_save_and_render(admin_client):
    _login(admin_client)
    response = admin_client.post(
        "/admin/letterhead",
        data={
            "csrf": _csrf(),
            "name": "Kop Uji",
            "line1": "Yayasan Lembaga Pendidikan Islam",
            "line2": "Sekolah Tinggi Ilmu Tarbiyah",
            "line3": "( STIT )",
            "line4": "Buntet Pesantren Cirebon",
            "line5": "Komplek Pondok Buntet Pesantren Telepon 085224108345",
            "accent_color": "#0d6b3f",
            "html_override": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    preview = admin_client.get("/admin/letterhead/preview")
    assert preview.status_code == 200
    assert "Yayasan Lembaga Pendidikan Islam" in preview.text
    assert "( STIT )" in preview.text
    assert "Telepon 085224108345" in preview.text
    assert "#0d6b3f" in preview.text


def test_letter_style_save_and_preview(admin_client):
    _login(admin_client)
    page = admin_client.get("/admin/letter-style")
    assert page.status_code == 200
    assert "Tipografi Surat" in page.text

    saved = admin_client.post(
        "/admin/letter-style",
        data={
            "csrf": _csrf(),
            "body_font": "Arial",
            "body_size_pt": "11",
            "line_height": "1.4",
            "paragraph_gap_pt": "8",
            "margin_top_cm": "1.8",
            "margin_bottom_cm": "2",
            "margin_left_cm": "2.5",
            "margin_right_cm": "2.2",
            "kop_gap_mm": "7",
            "kop_line1_size_pt": "12",
            "kop_main_size_pt": "15",
            "kop_address_size_pt": "9",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303

    preview = admin_client.get("/admin/letter-style/preview")
    assert preview.status_code == 200
    assert "Arial" in preview.text
    assert "font-size: 11pt" in preview.text
    assert "line-height: 1.4" in preview.text
    assert "margin: 1.8cm 2.2cm 2cm 2.5cm" in preview.text
    assert "font-size: 15pt" in preview.text
    assert "gap: 7mm" in preview.text

    page = admin_client.get("/admin/letter-style")
    assert 'value="11.0"' in page.text or 'value="11"' in page.text


def test_mailer_builds_message_with_attachment(monkeypatch):
    from tte_core import mailer

    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            captured["ehlo"] = True

        def starttls(self, context=None):
            captured["tls"] = True

        def login(self, user, password):
            captured["login"] = (user, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    config = mailer.SmtpConfig(
        enabled=True,
        host="smtp.gmail.com",
        port=587,
        username="pengirim@gmail.com",
        password="app-password",
        from_name="STIT Buntet Pesantren",
        from_email="",
        use_ssl=False,
        timeout=20,
        on_approve=True,
        on_generate=False,
        on_signed=True,
        institution="STIT Buntet Pesantren",
        public_base_url="http://testserver",
    )
    mailer.send_email(
        config,
        to="pemohon@example.test",
        subject="Surat Uji",
        text="Isi email",
        html_body="<p>Isi email</p>",
        attachments=[("surat.pdf", b"%PDF-1.4", "application/pdf")],
    )

    assert captured["host"] == "smtp.gmail.com"
    assert captured["port"] == 587
    assert captured["tls"] is True
    assert captured["login"] == ("pengirim@gmail.com", "app-password")
    message = captured["message"]
    assert message["To"] == "pemohon@example.test"
    assert message["Subject"] == "Surat Uji"
    assert any(
        part.get_filename() == "surat.pdf"
        for part in message.iter_attachments()
    )

    disabled = mailer.SmtpConfig(
        enabled=False, host="smtp.gmail.com", port=587, username="",
        password="", from_name="x", from_email="", use_ssl=False,
        timeout=20, on_approve=False, on_generate=False, on_signed=False,
        institution="x", public_base_url="http://testserver",
    )
    with pytest.raises(mailer.MailError):
        mailer.send_email(
            disabled, to="a@b.c", subject="x", text="y"
        )


def test_email_page_and_disabled_notice(admin_client):
    _login(admin_client)
    page = admin_client.get("/admin/email")
    assert page.status_code == 200
    assert "Email &amp; SMTP" in page.text
    assert "Belum aktif" in page.text

    sent = admin_client.post(
        "/admin/email/test",
        data={"csrf": _csrf(), "to": "uji@example.test"},
        follow_redirects=True,
    )
    assert sent.status_code == 200
    assert "belum aktif" in sent.text.lower()


def test_email_settings_saved_from_gui(admin_client, monkeypatch):
    _login(admin_client)

    saved = admin_client.post(
        "/admin/email/save",
        data={
            "csrf": _csrf(),
            "host": "smtp.gmail.com",
            "port": "587",
            "username": "pengirim@gmail.com",
            "password": "rahasia-app-password",
            "from_name": "STIT Buntet Pesantren",
            "from_email": "pengirim@gmail.com",
            "timeout": "20",
            "enabled": "1",
            "on_approve": "1",
            "on_generate": "1",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303

    page = admin_client.get("/admin/email")
    assert page.status_code == 200
    assert "Siap kirim" in page.text
    # Password tidak boleh muncul di HTML.
    assert "rahasia-app-password" not in page.text
    assert "tersimpan" in page.text

    from tte_core.db import get_session_factory
    from tte_core.mailer import get_email_config

    db = get_session_factory()()
    config = get_email_config(db)
    assert config.ready is True
    assert config.username == "pengirim@gmail.com"
    assert config.password == "rahasia-app-password"
    db.close()

    # Simpan ulang tanpa mengisi password: nilai lama harus dipertahankan.
    admin_client.post(
        "/admin/email/save",
        data={
            "csrf": _csrf(),
            "host": "smtp.gmail.com",
            "port": "587",
            "username": "pengirim@gmail.com",
            "password": "",
            "from_name": "STIT Buntet Pesantren",
            "from_email": "",
            "timeout": "20",
            "enabled": "1",
            "on_approve": "1",
            "on_generate": "1",
        },
        follow_redirects=False,
    )
    db = get_session_factory()()
    config = get_email_config(db)
    assert config.password == "rahasia-app-password"
    db.close()

    # Kirim email uji memakai SMTP palsu.
    from tte_core import mailer

    delivered = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            delivered["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            delivered["login"] = (user, password)

        def send_message(self, message):
            delivered["to"] = message["To"]

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    sent = admin_client.post(
        "/admin/email/test",
        data={"csrf": _csrf(), "to": "uji@example.test"},
        follow_redirects=False,
    )
    assert sent.status_code == 303
    assert "terkirim" in sent.headers["location"]
    assert delivered["login"] == ("pengirim@gmail.com", "rahasia-app-password")
    assert delivered["to"] == "uji@example.test"


def test_smtp_mode_follows_port(monkeypatch):
    from tte_core import mailer

    used = {}

    class FakeClient:
        def __init__(self, mode):
            used["mode"] = mode

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            used["starttls"] = True

        def login(self, user, password):
            pass

        def send_message(self, message):
            pass

    def fake_smtp(host, port, timeout=None):
        return FakeClient("starttls")

    def fake_ssl(host, port, timeout=None, context=None):
        return FakeClient("ssl")

    monkeypatch.setattr(mailer.smtplib, "SMTP", fake_smtp)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", fake_ssl)

    def make(port, use_ssl):
        return mailer.SmtpConfig(
            enabled=True, host="smtp.gmail.com", port=port,
            username="a@gmail.com", password="x", from_name="x",
            from_email="", use_ssl=use_ssl, timeout=10,
            on_approve=True, on_generate=False, on_signed=True,
            institution="x", public_base_url="http://testserver",
        )

    # Port 587 dengan opsi SSL keliru tetap memakai STARTTLS.
    mailer.send_email(make(587, True), to="b@c.d", subject="x", text="y")
    assert used["mode"] == "starttls"
    assert used.get("starttls") is True

    # Port 465 selalu SSL langsung.
    used.clear()
    mailer.send_email(make(465, False), to="b@c.d", subject="x", text="y")
    assert used["mode"] == "ssl"


def test_email_sent_only_after_all_signers(admin_client, monkeypatch):
    _login(admin_client)

    from tte_core import mailer

    delivered = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            delivered.append(message)

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    admin_client.post(
        "/admin/email/save",
        data={
            "csrf": _csrf(),
            "host": "smtp.gmail.com",
            "port": "587",
            "username": "pengirim@gmail.com",
            "password": "app-password",
            "from_name": "STIT Buntet Pesantren",
            "from_email": "",
            "timeout": "20",
            "security": "starttls",
            "enabled": "1",
            "on_approve": "1",
            "on_signed": "1",
        },
        follow_redirects=False,
    )

    institution_id, faculty_id, program_id = _create_master(admin_client)
    letter_type = f"Surat Multi Email {uuid.uuid4().hex[:6]}"
    admin_client.post(
        "/admin/letter-templates/save",
        data={
            "csrf": _csrf(),
            "letter_type": letter_type,
            "name": "Format multi email",
            "body_html": "<p>Halo [Nama].</p>[blok_ttd]",
            "signer_id": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import EmailLog, LetterRequest, SignRequest, SignerProfile

    signer_ids = []
    for index in (1, 2):
        nip = f"MULTI-{index}-{uuid.uuid4().hex[:8]}"
        admin_client.post(
            "/admin/signers",
            data={
                "csrf": _csrf(),
                "name": f"Penanda Multi {index}",
                "jabatan": f"Jabatan {index}",
                "nip": nip,
                "email": f"multi{index}@example.test",
            },
            follow_redirects=False,
        )
        db = get_session_factory()()
        signer_ids.append(
            db.execute(
                select(SignerProfile.id).where(SignerProfile.nip == nip)
            ).scalar_one()
        )
        db.close()

    request_no = _submit_letter(
        admin_client, institution_id, faculty_id, program_id, letter_type
    )
    db = get_session_factory()()
    letter_id = db.execute(
        select(LetterRequest.id).where(LetterRequest.request_no == request_no)
    ).scalar_one()
    db.close()

    admin_client.post(
        f"/admin/letters/{letter_id}/verify",
        data={"csrf": _csrf(), "next": f"/admin/letters/{letter_id}"},
        follow_redirects=False,
    )
    admin_client.post(
        f"/admin/letters/{letter_id}/approve",
        data={
            "csrf": _csrf(),
            "letter_no": f"B-{uuid.uuid4().hex[:6]}/MULTI/2026",
            "next": f"/admin/letters/{letter_id}",
        },
        follow_redirects=False,
    )

    generated = admin_client.post(
        f"/admin/letters/{letter_id}/generate",
        data={"csrf": _csrf(), "signer_id": signer_ids[0]},
        follow_redirects=False,
    )
    assert generated.status_code == 303
    document_id = generated.headers["location"].split("/admin/documents/")[1].split("?")[0]

    for signer_id in signer_ids:
        admin_client.post(
            f"/admin/documents/{document_id}/requests",
            data={"csrf": _csrf(), "signer_id": signer_id, "reason": "Pengesahan"},
            follow_redirects=False,
        )

    db = get_session_factory()()
    request_ids = [
        row
        for row in db.execute(
            select(SignRequest.id)
            .where(SignRequest.document_id == document_id)
            .order_by(SignRequest.created_at.asc())
        ).scalars()
    ]
    db.close()
    assert len(request_ids) == 2

    from tte_core import operations

    captured_modes = []
    real_sign = operations.sign_pdf_bytes

    def spy_sign(*args, **kwargs):
        captured_modes.append(kwargs.get("multi_mode"))
        return real_sign(*args, **kwargs)

    monkeypatch.setattr(operations, "sign_pdf_bytes", spy_sign)

    for request_id in request_ids:
        admin_client.post(
            f"/admin/requests/{request_id}/consent",
            data={"csrf": _csrf()},
            follow_redirects=False,
        )

    # Tanda tangan pertama: surat belum boleh terbit dan belum ada email.
    admin_client.post(
        f"/admin/requests/{request_ids[0]}/sign",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    db = get_session_factory()()
    letter = db.get(LetterRequest, letter_id)
    assert letter.status == "approved"
    signed_logs = db.execute(
        select(EmailLog).where(
            EmailLog.letter_id == letter_id, EmailLog.kind == "signed"
        )
    ).scalars().all()
    assert signed_logs == []
    db.close()

    # Tanda tangan terakhir: surat terbit dan email bertanda tangan terkirim.
    admin_client.post(
        f"/admin/requests/{request_ids[1]}/sign",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    db = get_session_factory()()
    letter = db.get(LetterRequest, letter_id)
    assert letter.status == "signed"
    signed_logs = db.execute(
        select(EmailLog).where(
            EmailLog.letter_id == letter_id, EmailLog.kind == "signed"
        )
    ).scalars().all()
    assert len(signed_logs) == 1
    assert signed_logs[0].status == "sent"
    assert signed_logs[0].has_attachment is True
    generated_logs = db.execute(
        select(EmailLog).where(
            EmailLog.letter_id == letter_id, EmailLog.kind == "generated"
        )
    ).scalars().all()
    assert generated_logs == []
    db.close()

    # Membuka halaman detail berulang tidak mengirim email kedua.
    before = len(delivered)
    admin_client.get(f"/admin/letters/{letter_id}")
    admin_client.get(f"/admin/letters/{letter_id}")
    assert len(delivered) == before

    # Multi penanda tangan: cap anonim (multi_mode=True) pada semua tanda tangan.
    assert captured_modes == [True, True]


def test_email_sent_after_single_signer(admin_client, monkeypatch):
    _login(admin_client)
    from tte_core import mailer

    delivered = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            delivered.append(message)

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    admin_client.post(
        "/admin/email/save",
        data={
            "csrf": _csrf(),
            "host": "smtp.gmail.com",
            "port": "587",
            "username": "pengirim@gmail.com",
            "password": "app-password",
            "from_name": "STIT Buntet Pesantren",
            "timeout": "20",
            "security": "starttls",
            "enabled": "1",
            "on_approve": "1",
            "on_signed": "1",
        },
        follow_redirects=False,
    )

    institution_id, faculty_id, program_id = _create_master(admin_client)
    letter_type = f"Surat Tunggal {uuid.uuid4().hex[:6]}"
    admin_client.post(
        "/admin/letter-templates/save",
        data={
            "csrf": _csrf(),
            "letter_type": letter_type,
            "name": "Format tunggal",
            "body_html": "<p>Halo [Nama].</p>[blok_ttd]",
            "signer_id": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )

    nip = f"TUNGGAL-{uuid.uuid4().hex[:8]}"
    admin_client.post(
        "/admin/signers",
        data={
            "csrf": _csrf(),
            "name": "Penanda Tunggal",
            "jabatan": "Rektor",
            "nip": nip,
            "email": "tunggal@example.test",
        },
        follow_redirects=False,
    )

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import EmailLog, LetterRequest, SignRequest, SignerProfile

    db = get_session_factory()()
    signer_id = db.execute(
        select(SignerProfile.id).where(SignerProfile.nip == nip)
    ).scalar_one()
    db.close()

    request_no = _submit_letter(
        admin_client, institution_id, faculty_id, program_id, letter_type
    )
    db = get_session_factory()()
    letter_id = db.execute(
        select(LetterRequest.id).where(LetterRequest.request_no == request_no)
    ).scalar_one()
    db.close()

    admin_client.post(
        f"/admin/letters/{letter_id}/verify",
        data={"csrf": _csrf(), "next": f"/admin/letters/{letter_id}"},
        follow_redirects=False,
    )
    admin_client.post(
        f"/admin/letters/{letter_id}/approve",
        data={
            "csrf": _csrf(),
            "letter_no": f"B-{uuid.uuid4().hex[:6]}/TUNGGAL/2026",
            "next": f"/admin/letters/{letter_id}",
        },
        follow_redirects=False,
    )
    generated = admin_client.post(
        f"/admin/letters/{letter_id}/generate",
        data={"csrf": _csrf(), "signer_id": signer_id},
        follow_redirects=False,
    )
    document_id = generated.headers["location"].split("/admin/documents/")[1].split("?")[0]

    admin_client.post(
        f"/admin/documents/{document_id}/requests",
        data={"csrf": _csrf(), "signer_id": signer_id, "reason": "Pengesahan"},
        follow_redirects=False,
    )
    db = get_session_factory()()
    request_id = db.execute(
        select(SignRequest.id).where(SignRequest.document_id == document_id)
    ).scalar_one()
    db.close()

    admin_client.post(
        f"/admin/requests/{request_id}/consent",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )

    from tte_core import operations

    captured_modes = []
    real_sign = operations.sign_pdf_bytes

    def spy_sign(*args, **kwargs):
        captured_modes.append(kwargs.get("multi_mode"))
        return real_sign(*args, **kwargs)

    monkeypatch.setattr(operations, "sign_pdf_bytes", spy_sign)

    signed = admin_client.post(
        f"/admin/requests/{request_id}/sign",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert signed.status_code == 303

    db = get_session_factory()()
    letter = db.get(LetterRequest, letter_id)
    assert letter.status == "signed"
    logs = db.execute(
        select(EmailLog).where(
            EmailLog.letter_id == letter_id, EmailLog.kind == "signed"
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "sent"
    assert logs[0].has_attachment is True
    db.close()
    assert len(delivered) >= 2  # pemberitahuan persetujuan + surat final

    # Penanda tangan tunggal: cap memakai mode identitas (bukan anonim).
    assert captured_modes == [False]
