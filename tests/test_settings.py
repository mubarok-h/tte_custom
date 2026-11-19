from __future__ import annotations

import uuid
from io import BytesIO

from tte_core.admin_auth import csrf_token
from tte_core.config import get_settings


def _csrf() -> str:
    return csrf_token(get_settings())


def _login(client, username: str | None = None, password: str | None = None) -> None:
    response = client.post(
        "/admin/login",
        data={
            "username": username or get_settings().admin_username,
            "password": password or get_settings().admin_token,
            "csrf": _csrf(),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _png() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (96, 96), (13, 107, 63)).save(buffer, format="PNG")
    return buffer.getvalue()


def _cleanup() -> None:
    from sqlalchemy import delete

    from tte_core.db import get_session_factory
    from tte_core.models import SiteSettings

    db = get_session_factory()()
    db.execute(delete(SiteSettings))
    db.commit()
    db.close()


def test_settings_page_requires_permission(client):
    _login(client)

    username = f"brand_{uuid.uuid4().hex[:6]}"
    password = "brand-pass-123"
    created = client.post(
        "/admin/users",
        data={
            "csrf": _csrf(),
            "name": "Operator Branding",
            "username": username,
            "role": "operator",
            "password": password,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(client, username, password)
    denied = client.get("/admin/settings", follow_redirects=False)
    assert denied.status_code == 303
    assert "/admin?" in denied.headers["location"]

    client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(client)

    from urllib.parse import urlencode

    granted = client.post(
        "/admin/permissions",
        content=urlencode(
            [
                ("csrf", _csrf()),
                ("perms_operator", "letters"),
                ("perms_operator", "settings"),
            ]
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert granted.status_code == 303

    client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(client, username, password)
    allowed = client.get("/admin/settings")
    assert allowed.status_code == 200
    assert "Identitas &amp; Aplikasi" in allowed.text

    client.post("/admin/logout", data={"csrf": _csrf()}, follow_redirects=False)
    _login(client)


def test_identity_app_and_homepage_settings(client):
    _login(client)
    _cleanup()

    institution = f"Universitas Uji {uuid.uuid4().hex[:6]}"
    saved = client.post(
        "/admin/settings/save",
        data={
            "csrf": _csrf(),
            "tab": "identity",
            "institution_name": institution,
            "foundation_name": "Yayasan Uji",
            "short_name": "UU",
            "city": "Kota Uji",
            "address": "Jalan Uji 1",
            "phone": "0812000000",
            "email_pub": "info@uji.test",
            "website": "uji.test",
        },
        files={
            "logo": ("logo.png", _png(), "image/png"),
            "favicon": ("favicon.png", _png(), "image/png"),
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303

    page = client.get("/admin/settings")
    assert institution in page.text
    assert "/branding/logo-" in page.text
    assert "/branding/favicon-32-" in page.text

    home = client.get("/")
    assert institution in home.text
    assert "/branding/logo-" in home.text
    assert "/branding/favicon-32-" in home.text

    # Halaman aplikasi: nama aplikasi, kredit footer, pengumuman.
    app_saved = client.post(
        "/admin/settings/save",
        data={
            "csrf": _csrf(),
            "tab": "app",
            "app_name": "Sistem Uji",
            "app_tagline": "Layanan Uji",
            "footer_credit": "Tim Uji",
            "announcement": "Pengumuman Uji",
        },
        follow_redirects=False,
    )
    assert app_saved.status_code == 303
    home = client.get("/")
    assert "Tim Uji" in home.text
    assert "Pengumuman Uji" in home.text

    # Beranda: nonaktifkan kartu cepat.
    home_saved = client.post(
        "/admin/settings/save",
        data={
            "csrf": _csrf(),
            "tab": "home",
            "site_title": "Situs Uji Resmi",
            "brand_name": "TTE Uji!",
            "brand_subtitle": "Universitas Uji · Tanda Tangan Elektronik",
            "hero_pill": "Pill Uji",
            "hero_title": "Judul Uji",
            "hero_highlight": "Sorotan Uji",
            "hero_description": "Deskripsi Uji",
        },
        follow_redirects=False,
    )
    assert home_saved.status_code == 303
    home = client.get("/")
    assert "Judul Uji" in home.text
    assert "Sorotan Uji" in home.text
    assert "Situs Uji Resmi" in home.text
    assert "TTE Uji!" in home.text
    assert "Universitas Uji · Tanda Tangan Elektronik" in home.text
    assert 'class="quick-grid"' not in home.text

    # Salin identitas ke kop surat.
    copied = client.post(
        "/admin/settings/copy-to-letterhead",
        data={"csrf": _csrf()},
        follow_redirects=False,
    )
    assert copied.status_code == 303

    from sqlalchemy import select

    from tte_core.db import get_session_factory
    from tte_core.models import LetterHead, SiteSettings

    db = get_session_factory()()
    head = db.execute(select(LetterHead).limit(1)).scalar_one_or_none()
    assert head is not None
    assert head.line2 == institution
    assert head.logo_path and head.logo_path.endswith(".png")
    row = db.execute(select(SiteSettings)).scalar_one()
    logo = row.logo_path
    db.close()

    media = client.get(f"/branding/{logo}")
    assert media.status_code == 200

    _cleanup()
