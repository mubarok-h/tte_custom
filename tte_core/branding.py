from __future__ import annotations

"""Resolver identitas lembaga dan pengaturan aplikasi.

Nilai diambil berurutan: database (`site_settings`) -> `.env` -> default.
Jika sebuah field kosong di database, nilai dari `.env` dipakai sehingga
instalasi lama tetap berjalan tanpa perubahan.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import SiteSettings

DEFAULT_HERO_PILL = "Layanan Persuratan & TTE Internal · Aktif 24 Jam"
DEFAULT_HERO_TITLE = "Urus Surat Akademik"
DEFAULT_HERO_HIGHLIGHT = "Lebih Cepat & Terverifikasi."
DEFAULT_HERO_DESCRIPTION = (
    "Ajukan surat keterangan aktif kuliah, surat pengantar penelitian, atau "
    "surat akademik lain langsung dari ponsel. Tata Usaha memproses, lalu "
    "dokumen diterbitkan dengan tanda tangan elektronik {institution}."
)
DEFAULT_BRAND_NAME = "TTE!"
DEFAULT_APP_TAGLINE = "Tanda Tangan Elektronik"
DEFAULT_FOOTER_CREDIT = "Pusat Data"
DEFAULT_LOGO = "/static/brand/stit-logo.png"
DEFAULT_FAVICON_32 = "/static/brand/stit-favicon-32.png?v=2"
DEFAULT_FAVICON_192 = "/static/brand/stit-favicon-192.png?v=2"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class Branding:
    institution: str
    foundation: str
    short_name: str
    location: str
    address: str
    phone: str
    email: str
    website: str
    logo_url: str
    favicon_32_url: str
    favicon_192_url: str
    app_name: str
    site_title: str
    brand_name: str
    brand_subtitle: str
    app_tagline: str
    footer_credit: str
    announcement: str
    hero_pill: str
    hero_title: str
    hero_highlight: str
    hero_description: str
    show_quick_links: bool
    show_flow: bool
    show_notice: bool


def branding_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.data_dir / "branding"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _media_url(filename: str | None) -> str | None:
    if not filename:
        return None
    path = branding_dir() / Path(filename).name
    if not path.exists():
        return None
    version = int(path.stat().st_mtime)
    return f"/branding/{path.name}?v={version}"


def get_site_settings(db: Session) -> SiteSettings | None:
    return db.execute(
        select(SiteSettings).order_by(SiteSettings.updated_at.desc()).limit(1)
    ).scalar_one_or_none()


def load_branding(
    db: Session, settings: Settings | None = None
) -> Branding:
    settings = settings or get_settings()
    row = get_site_settings(db)

    institution = (row.institution_name if row else "") or settings.institution
    location = (row.city if row else "") or settings.location
    app_name = (row.app_name if row else "") or settings.app_name
    logo = _media_url(row.logo_path) if row else None
    favicon_32 = _media_url(row.favicon_32_path) if row else None
    favicon_192 = _media_url(row.favicon_192_path) if row else None

    hero_description = (row.hero_description if row else "") or (
        DEFAULT_HERO_DESCRIPTION.format(institution=institution)
    )

    return Branding(
        institution=institution,
        foundation=(row.foundation_name if row else "") or "",
        short_name=(row.short_name if row else "") or "",
        location=location,
        address=(row.address if row else "") or "",
        phone=(row.phone if row else "") or "",
        email=(row.email if row else "") or "",
        website=(row.website if row else "") or "",
        logo_url=logo or DEFAULT_LOGO,
        favicon_32_url=favicon_32 or DEFAULT_FAVICON_32,
        favicon_192_url=favicon_192 or DEFAULT_FAVICON_192,
        app_name=app_name,
        site_title=(row.site_title if row else "") or "",
        brand_name=(row.brand_name if row else "") or DEFAULT_BRAND_NAME,
        brand_subtitle=(row.brand_subtitle if row else "")
        or f"{institution} · {DEFAULT_APP_TAGLINE}",
        app_tagline=(row.app_tagline if row else "") or DEFAULT_APP_TAGLINE,
        footer_credit=(row.footer_credit if row else "")
        or DEFAULT_FOOTER_CREDIT,
        announcement=(row.announcement if row else "") or "",
        hero_pill=(row.hero_pill if row else "") or DEFAULT_HERO_PILL,
        hero_title=(row.hero_title if row else "") or DEFAULT_HERO_TITLE,
        hero_highlight=(row.hero_highlight if row else "")
        or DEFAULT_HERO_HIGHLIGHT,
        hero_description=hero_description,
        show_quick_links=bool(row.show_quick_links) if row else True,
        show_flow=bool(row.show_flow) if row else True,
        show_notice=bool(row.show_notice) if row else True,
    )
