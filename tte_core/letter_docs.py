from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    LetterHead,
    LetterRequest,
    LetterStyle,
    LetterTemplate,
    SignerProfile,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOGO = _PROJECT_ROOT / "tte_core" / "static" / "brand" / "stit-logo.png"

BULAN = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)

# Kelompok keyword yang ditampilkan pada bantuan editor.
KEYWORD_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Identitas pemohon",
        (
            ("nama", "Nama lengkap", "M. Mubarok"),
            ("nim", "NIM", "2026001234"),
            ("tempat_lahir", "Tempat lahir", "Cirebon"),
            ("tanggal_lahir", "Tanggal lahir", "12 Mei 2004"),
            ("email", "Email", "mahasiswa@example.ac.id"),
            ("telepon", "Nomor WhatsApp", "081234567890"),
            ("alamat", "Alamat", "Buntet Pesantren, Cirebon"),
        ),
    ),
    (
        "Data akademik",
        (
            ("lembaga", "Nama lembaga", "STIT Buntet Pesantren"),
            ("fakultas", "Fakultas", "Fakultas Tarbiyah"),
            ("prodi", "Program studi", "Manajemen Pendidikan Islam"),
            ("semester", "Semester", "6"),
            ("tahun_akademik", "Tahun akademik", "2026/2027 Ganjil"),
        ),
    ),
    (
        "Data dosen",
        (
            ("nidn", "NIDN/NIP dosen", "0123456789"),
            ("jabatan_dosen", "Jabatan akademik/fungsional", "Lektor"),
            ("status_kepegawaian", "Status kepegawaian", "Dosen Tetap"),
        ),
    ),
    (
        "Isi permohonan",
        (
            ("jenis_surat", "Jenis surat", "Surat Keterangan Aktif Kuliah"),
            ("tujuan", "Tujuan surat", "Instansi tujuan"),
            ("keperluan", "Keperluan", "Pengajuan beasiswa"),
            ("judul_penelitian", "Judul penelitian", "Judul penelitian mahasiswa"),
        ),
    ),
    (
        "Surat",
        (
            ("nomor_surat", "Nomor surat resmi", "B-1204/Un.05/Ps/PP.00.9/11/2026"),
            ("nomor_permohonan", "Nomor permohonan", "REQ-20261119-0001"),
            ("tanggal_surat", "Tanggal surat", "19 November 2026"),
            ("kota", "Kota penerbitan", "Cirebon"),
            ("tahun", "Tahun berjalan", "2026"),
        ),
    ),
    (
        "Penanda tangan",
        (
            ("nama_penandatangan", "Nama pejabat", "M. Mubarok"),
            (
                "jabatan_penandatangan",
                "Jabatan pejabat",
                "Rektor STIT Buntet Pesantren",
            ),
            ("nip_penandatangan", "NIP pejabat", "000000000"),
        ),
    ),
)

KEYWORDS: dict[str, tuple[str, str]] = {
    key: (label, sample)
    for _, items in KEYWORD_GROUPS
    for key, label, sample in items
}

ALIASES = {
    "nama_pemohon": "nama",
    "nama_mahasiswa": "nama",
    "program_studi": "prodi",
    "program": "prodi",
    "jurusan": "prodi",
    "nama_lembaga": "lembaga",
    "institusi": "lembaga",
    "nama_fakultas": "fakultas",
    "tgl_lahir": "tanggal_lahir",
    "tgl_surat": "tanggal_surat",
    "no_surat": "nomor_surat",
    "pejabat": "nama_penandatangan",
    "jabatan": "jabatan_penandatangan",
    "nip": "nip_penandatangan",
    "tempat": "kota",
}

# Keyword yang menghasilkan HTML mentah (tidak di-escape).
RAW_KEYWORDS = {
    "blok_ttd",
    "blok_ttd_2",
    "blok_ttd_3",
    "blok_ttd_4",
}

_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{1,60})\]")


def normalize_key(token: str) -> str:
    key = token.strip().lower()
    key = re.sub(r"[\s\-]+", "_", key)
    key = re.sub(r"[^a-z0-9_]", "", key)
    return ALIASES.get(key, key)


def apply_case(token: str, value: str) -> str:
    """Terapkan kapitalisasi sesuai bentuk token di dalam kurung."""
    if not value:
        return ""
    letters = [ch for ch in token if ch.isalpha()]
    if not letters:
        return value
    if all(ch.isupper() for ch in letters):
        return value.upper()
    if all(ch.islower() for ch in letters):
        return value.lower()
    # Campuran / Title Case: huruf awal tiap kata kapital, sisanya kecil.
    # Singkatan pendek (STIT, NIP, M.) dipertahankan agar tidak rusak.
    def title_word(word: str) -> str:
        if not word:
            return ""
        if 1 < len(word) <= 6 and word.isupper():
            return word
        return word[:1].upper() + word[1:].lower()

    return " ".join(title_word(word) for word in value.split(" "))


def _format_tanggal(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.day} {BULAN[value.month - 1]} {value.year}"
    return str(value)


def _html_value(value: str) -> str:
    escaped = html.escape(value, quote=False)
    return escaped.replace("\n", "<br>")


def render_text(text: str, context: dict[str, str]) -> tuple[str, list[str]]:
    """Ganti seluruh keyword. Kembalikan (hasil, daftar keyword tak dikenal)."""
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        key = normalize_key(token)
        if key in RAW_KEYWORDS:
            raw = context.get(key, "")
            return raw
        if key not in context:
            if token.strip() and token not in unknown:
                unknown.append(token)
            return match.group(0)
        return _html_value(apply_case(token, str(context.get(key, ""))))

    return _PLACEHOLDER.sub(replace, text), unknown


def _ttd_date(
    letter: LetterRequest, settings: Settings, branding=None
) -> str:
    tanggal = _format_tanggal(
        letter.approved_at
        or letter.verified_at
        or letter.submitted_at
        or datetime.now(timezone.utc)
    )
    city = branding.location if branding else settings.location
    return f"{_html_value(city)}, {_html_value(tanggal)}"


def signature_block(
    letter: LetterRequest,
    signer: SignerProfile | None,
    settings: Settings,
    branding=None,
) -> str:
    if signer is None:
        return ""
    return (
        '<div class="ttd">'
        f'<div class="ttd-place">{_ttd_date(letter, settings, branding)}</div>'
        f'<div class="ttd-title">{_html_value(signer.jabatan)}</div>'
        '<div class="ttd-space"></div>'
        f'<div class="ttd-name">{_html_value(signer.name)}</div>'
        f'<div class="ttd-nip">NIP. {_html_value(signer.nip)}</div>'
        "</div>"
    )


def signature_block_multi(
    letter: LetterRequest,
    people: list[SignerProfile],
    settings: Settings,
    columns: int,
    branding=None,
) -> str:
    """Blok tanda tangan 1-4 kolom.

    - 2 penanda tangan: berdampingan dalam satu baris.
    - 3 penanda tangan: 2 berdampingan, satu di baris bawah tepat di tengah.
    - 4 penanda tangan: 2 berdampingan, 2 lagi di baris bawah.
    """
    if columns <= 1:
        return signature_block(
            letter, people[0] if people else None, settings, branding
        )

    def cell(index: int) -> str:
        person = people[index] if index < len(people) else None
        if person is None:
            return (
                '<div class="ttd-col">'
                '<div class="ttd-title">&nbsp;</div>'
                '<div class="ttd-space"></div>'
                '<div class="ttd-name">&nbsp;</div>'
                '<div class="ttd-nip">&nbsp;</div>'
                "</div>"
            )
        return (
            '<div class="ttd-col">'
            f'<div class="ttd-title">{_html_value(person.jabatan)}</div>'
            '<div class="ttd-space"></div>'
            f'<div class="ttd-name">{_html_value(person.name)}</div>'
            f'<div class="ttd-nip">NIP. {_html_value(person.nip)}</div>'
            "</div>"
        )

    cells = [cell(index) for index in range(columns)]
    if columns == 3:
        rows = [cells[:2], cells[2:]]
    else:
        rows = [cells[index : index + 2] for index in range(0, columns, 2)]

    rendered_rows = []
    for row in rows:
        modifier = " ttd-row-single" if len(row) == 1 else ""
        rendered_rows.append(
            f'<div class="ttd-row{modifier}">{"".join(row)}</div>'
        )

    return (
        '<div class="ttd-multi">'
        f'<div class="ttd-date">{_ttd_date(letter, settings, branding)}</div>'
        f'{"".join(rendered_rows)}'
        "</div>"
    )


def build_context(
    letter: LetterRequest,
    signer: SignerProfile | None,
    settings: Settings,
    signers: list[SignerProfile] | None = None,
    branding=None,
) -> dict[str, str]:
    tanggal = _format_tanggal(
        letter.approved_at
        or letter.verified_at
        or letter.submitted_at
        or datetime.now(timezone.utc)
    )
    tahun = ""
    if letter.approved_at or letter.submitted_at:
        source = letter.approved_at or letter.submitted_at
        tahun = str(source.year)
    else:
        tahun = str(datetime.now(timezone.utc).year)

    people = [person for person in (signers or []) if person is not None]
    if not people and signer is not None:
        people = [signer]

    values = {
        "nama": letter.applicant_name or "",
        "nim": letter.nim or "",
        "nidn": letter.nidn or "",
        "jabatan_dosen": letter.jabatan or "",
        "status_kepegawaian": letter.employment_status or "",
        "tempat_lahir": letter.birth_place or "",
        "tanggal_lahir": _format_tanggal(letter.birth_date),
        "email": letter.email or "",
        "telepon": letter.phone or "",
        "alamat": letter.address or "",
        "lembaga": letter.institution_name
        or (branding.institution if branding else settings.institution),
        "fakultas": letter.faculty_name or "",
        "prodi": letter.program_name or "",
        "semester": letter.semester or "",
        "tahun_akademik": letter.academic_year or "",
        "jenis_surat": letter.letter_type or "",
        "tujuan": letter.purpose or "",
        "keperluan": letter.notes or "",
        "judul_penelitian": letter.research_title or "",
        "nomor_surat": letter.letter_no or "",
        "nomor_permohonan": letter.request_no or "",
        "tanggal_surat": tanggal,
        "kota": branding.location if branding else settings.location,
        "tahun": tahun,
        "nama_penandatangan": signer.name if signer else "",
        "jabatan_penandatangan": signer.jabatan if signer else "",
        "nip_penandatangan": signer.nip if signer else "",
        "blok_ttd": signature_block_multi(letter, people, settings, 1, branding),
        "blok_ttd_2": signature_block_multi(letter, people, settings, 2, branding),
        "blok_ttd_3": signature_block_multi(letter, people, settings, 3, branding),
        "blok_ttd_4": signature_block_multi(letter, people, settings, 4, branding),
    }
    return values


def sample_context(settings: Settings, branding=None) -> dict[str, str]:
    """Data contoh untuk pratinjau template di panel admin."""
    values = {key: sample for key, (_, sample) in KEYWORDS.items()}
    values["kota"] = branding.location if branding else settings.location
    values["lembaga"] = (
        branding.institution if branding else settings.institution
    )

    single = (
        '<div class="ttd">'
        f'<div class="ttd-place">{_html_value(values["kota"])}, '
        "19 November 2026</div>"
        '<div class="ttd-title">Jabatan Pejabat</div>'
        '<div class="ttd-space"></div>'
        '<div class="ttd-name">Nama Pejabat</div>'
        '<div class="ttd-nip">NIP. 000000000</div>'
        "</div>"
    )
    values["blok_ttd"] = single

    cell = (
        '<div class="ttd-col">'
        '<div class="ttd-title">Jabatan Pejabat</div>'
        '<div class="ttd-space"></div>'
        '<div class="ttd-name">Nama Pejabat</div>'
        '<div class="ttd-nip">NIP. 000000000</div>'
        "</div>"
    )
    layouts = {
        2: [f'<div class="ttd-row">{cell}{cell}</div>'],
        3: [
            f'<div class="ttd-row">{cell}{cell}</div>',
            f'<div class="ttd-row ttd-row-single">{cell}</div>',
        ],
        4: [
            f'<div class="ttd-row">{cell}{cell}</div>',
            f'<div class="ttd-row">{cell}{cell}</div>',
        ],
    }
    for count, key in ((2, "blok_ttd_2"), (3, "blok_ttd_3"), (4, "blok_ttd_4")):
        values[key] = (
            '<div class="ttd-multi">'
            f'<div class="ttd-date">{_html_value(values["kota"])}, '
            "19 November 2026</div>"
            f'{"".join(layouts[count])}'
            "</div>"
        )
    return values


def get_active_letterhead(db: Session) -> LetterHead | None:
    return (
        db.execute(
            select(LetterHead)
            .where(LetterHead.is_active.is_(True))
            .order_by(LetterHead.updated_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )


def logo_file(letterhead: LetterHead | None) -> Path | None:
    """Lokasi berkas logo aktif (atau logo bawaan aplikasi)."""
    path = Path(letterhead.logo_path) if letterhead and letterhead.logo_path else None
    if path is None or not path.exists():
        path = _DEFAULT_LOGO
    return path if path.exists() else None


def _logo_src(letterhead: LetterHead | None) -> str:
    path = logo_file(letterhead)
    if path is None:
        return ""
    return f"file://{path.resolve()}"


_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def accent_color(letterhead: LetterHead | None) -> str:
    value = (letterhead.accent_color if letterhead else "") or ""
    return value if _COLOR_PATTERN.match(value) else "#0d6b3f"


def render_letterhead(
    letterhead: LetterHead | None, logo_src: str | None = None
) -> str:
    if letterhead and (letterhead.html_override or "").strip():
        return letterhead.html_override.strip()

    accent = accent_color(letterhead)
    logo = logo_src if logo_src is not None else _logo_src(letterhead)
    logo_html = (
        f'<img class="kop-logo" src="{html.escape(logo, quote=True)}" alt="Logo">'
        if logo
        else ""
    )

    heading: list[str] = []
    if letterhead is not None:
        for index, raw in enumerate(
            (
                letterhead.line1,
                letterhead.line2,
                letterhead.line3,
                letterhead.line4,
            )
        ):
            if raw and raw.strip():
                css = "kop-line-1" if index == 0 else "kop-line-main"
                heading.append(
                    f'<div class="{css}">{_html_value(raw.strip())}</div>'
                )
    if not heading:
        heading = ['<div class="kop-line-main">Kop surat belum diatur.</div>']

    address = ""
    if letterhead is not None and (letterhead.line5 or "").strip():
        address = (
            f'<div class="kop-address" style="color:{accent};'
            f'border-bottom:1px solid {accent}">'
            f"{_html_value(letterhead.line5.strip())}</div>"
        )

    return (
        f'<div class="kop" style="border-bottom:2.5px solid {accent}">'
        f'<div class="kop-logo-wrap">{logo_html}</div>'
        f'<div class="kop-text" style="color:{accent}">{"".join(heading)}</div>'
        "</div>"
        f"{address}"
    )


FONT_STACKS: dict[str, str] = {
    "Times New Roman": '"Times New Roman", "Liberation Serif", serif',
    "Arial": 'Arial, "Liberation Sans", sans-serif',
    "Georgia": 'Georgia, "Noto Serif", serif',
    "Calibri": "Calibri, Carlito, sans-serif",
    "Liberation Serif": '"Liberation Serif", serif',
    "Noto Serif": '"Noto Serif", serif',
    "DejaVu Serif": '"DejaVu Serif", serif',
}

FONT_CHOICES = tuple(FONT_STACKS)


def default_style() -> LetterStyle:
    """Nilai awal tipografi surat (dipakai bila tabel belum terisi)."""
    return LetterStyle(
        body_font="Times New Roman",
        body_size_pt=12.0,
        line_height=1.55,
        paragraph_gap_pt=10.0,
        margin_top_cm=2.0,
        margin_bottom_cm=2.2,
        margin_left_cm=2.6,
        margin_right_cm=2.4,
        kop_gap_mm=4.0,
        kop_line1_size_pt=11.5,
        kop_main_size_pt=14.0,
        kop_address_size_pt=8.5,
    )


def get_active_style(db: Session) -> LetterStyle:
    row = db.execute(
        select(LetterStyle).order_by(LetterStyle.updated_at.desc()).limit(1)
    ).scalar_one_or_none()
    return row or default_style()


def _num(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def font_stack(family: str | None) -> str:
    key = (family or "").strip()
    if key in FONT_STACKS:
        return FONT_STACKS[key]
    # Nama font dari admin: bersihkan agar tidak menyuntik CSS.
    safe = re.sub(r"[^A-Za-z0-9 \-]", "", key).strip() or "Times New Roman"
    return f'"{safe}", serif'


def style_css(style: LetterStyle | None = None) -> str:
    s = style or default_style()
    size = _num(s.body_size_pt, 12.0, 8.0, 24.0)
    line = _num(s.line_height, 1.55, 1.0, 3.0)
    gap = _num(s.paragraph_gap_pt, 10.0, 0.0, 30.0)
    mt = _num(s.margin_top_cm, 2.0, 1.0, 5.0)
    mb = _num(s.margin_bottom_cm, 2.2, 1.0, 5.0)
    ml = _num(s.margin_left_cm, 2.6, 1.0, 5.0)
    mr = _num(s.margin_right_cm, 2.4, 1.0, 5.0)
    k1 = _num(s.kop_line1_size_pt, 11.5, 6.0, 24.0)
    km = _num(s.kop_main_size_pt, 14.0, 6.0, 30.0)
    ka = _num(s.kop_address_size_pt, 8.5, 5.0, 16.0)
    kg = _num(s.kop_gap_mm, 4.0, 0.0, 20.0)

    css = """
@page {
  size: A4;
  margin: @MTcm @MRcm @MBcm @MLcm;
  @bottom-center {
    content: "Halaman " counter(page) " dari " counter(pages);
    font-family: @FONT;
    font-size: 9pt;
    color: #555;
  }
}
* { box-sizing: border-box; }
body {
  font-family: @FONT;
  font-size: @SIZEpt;
  line-height: @LINE;
  color: #111;
  margin: 0;
}
.kop {
  display: flex;
  align-items: center;
  gap: @KOPGAPmm;
  padding-bottom: 6px;
  margin-bottom: 0;
}
.kop-logo-wrap { flex: 0 0 auto; }
.kop-logo { width: 80px; height: auto; }
.kop-text {
  flex: 1 1 auto;
  text-align: center;
  line-height: 1.18;
  text-transform: uppercase;
  font-weight: bold;
}
.kop-line-1 { font-size: @K1pt; letter-spacing: 0.01em; }
.kop-line-main { font-size: @KMpt; letter-spacing: 0.02em; }
.kop-address {
  text-align: center;
  font-size: @KApt;
  line-height: 1.3;
  padding: 2px 0 3px;
  margin-bottom: 16px;
}
.meta { margin: 4px 0 14px; }
.meta table { border-collapse: collapse; }
.meta td { padding: 0 6px 0 0; vertical-align: top; font-size: @SIZEpt; }
.body p { margin: 0 0 @GAPpt; text-align: justify; }
.body table.data { border-collapse: collapse; margin: 6px 0 12px 18px; }
.body table.data td { padding: 1px 6px 1px 0; vertical-align: top; }
.body ol, .body ul { margin: 0 0 @GAPpt 18px; padding-left: 16px; }
.ttd {
  margin-top: 26px;
  width: 260px;
  margin-left: auto;
  text-align: center;
  page-break-inside: avoid;
}
.ttd-place { font-size: @SIZEpt; }
.ttd-title { margin-top: 2px; }
.ttd-space { height: 58px; }
.ttd-name { font-weight: bold; text-decoration: underline; }
.ttd-nip { font-size: 11pt; }
.ttd-multi { margin-top: 26px; page-break-inside: avoid; }
.ttd-date { text-align: right; margin-bottom: 4px; }
.ttd-row {
  display: flex;
  gap: 18px;
  page-break-inside: avoid;
}
.ttd-row + .ttd-row { margin-top: 22px; }
.ttd-col { flex: 1 1 0; text-align: center; }
.ttd-row-single .ttd-col {
  flex: 0 0 calc(50% - 9px);
  margin: 0 auto;
}
.ttd-col {
  flex: 1 1 0;
  text-align: center;
}
h1, h2, h3, h4 { font-size: @SIZEpt; margin: 0 0 8px; }
.center { text-align: center; }
.right { text-align: right; }
"""
    replacements = {
        "@FONT": font_stack(s.body_font),
        "@SIZE": f"{size:g}",
        "@LINE": f"{line:g}",
        "@GAP": f"{gap:g}",
        "@MT": f"{mt:g}",
        "@MB": f"{mb:g}",
        "@ML": f"{ml:g}",
        "@MR": f"{mr:g}",
        "@K1": f"{k1:g}",
        "@KM": f"{km:g}",
        "@KA": f"{ka:g}",
        "@KOPGAP": f"{kg:g}",
    }
    for token, value in replacements.items():
        css = css.replace(token, value)
    return css


def render_letter_html(
    *,
    letter: LetterRequest,
    template: LetterTemplate,
    letterhead: LetterHead | None,
    signer: SignerProfile | None,
    settings: Settings,
    logo_src: str | None = None,
    style: LetterStyle | None = None,
    signers: list[SignerProfile] | None = None,
    branding=None,
) -> tuple[str, list[str]]:
    context = build_context(
        letter, signer, settings, signers=signers, branding=branding
    )
    body, unknown = render_text(template.body_html or "", context)
    meta_nomor = ""
    if letter.letter_no:
        meta_nomor = (
            '<div class="meta"><table>'
            f'<tr><td>Nomor</td><td>:</td><td>{html.escape(letter.letter_no)}</td></tr>'
            f'<tr><td>Hal</td><td>:</td><td>{html.escape(letter.letter_type)}</td></tr>'
            "</table></div>"
        )
    document = (
        "<!doctype html><html lang=\"id\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(letter.letter_type)}</title>"
        f"<style>{style_css(style)}</style></head><body>"
        f"{render_letterhead(letterhead, logo_src=logo_src)}"
        f"{meta_nomor}"
        f'<div class="body">{body}</div>'
        "</body></html>"
    )
    return document, unknown


def render_pdf(document_html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=document_html, base_url=str(_PROJECT_ROOT)).write_pdf()


def render_preview_html(
    body_html: str,
    context: dict[str, str],
    letterhead: LetterHead | None,
    logo_src: str | None = None,
    style: LetterStyle | None = None,
) -> tuple[str, list[str]]:
    """HTML utuh untuk pratinjau di browser (bukan PDF)."""
    body, unknown = render_text(body_html or "", context)
    document = (
        '<!doctype html><html lang="id"><head><meta charset="utf-8">'
        "<title>Pratinjau Surat</title>"
        f"<style>{style_css(style)}"
        "body{background:#fff;padding:28px 34px;max-width:900px;margin:0 auto;}"
        "</style></head><body>"
        f"{render_letterhead(letterhead, logo_src=logo_src)}"
        '<div class="meta"><table>'
        "<tr><td>Nomor</td><td>:</td><td>B-1204/Un.05/Ps/PP.00.9/11/2026</td></tr>"
        "<tr><td>Hal</td><td>:</td><td>Contoh Jenis Surat</td></tr>"
        "</table></div>"
        f'<div class="body">{body}</div>'
        "</body></html>"
    )
    return document, unknown


def render_head_preview(
    letterhead: LetterHead | None,
    logo_src: str | None = None,
    style: LetterStyle | None = None,
) -> str:
    return (
        '<!doctype html><html lang="id"><head><meta charset="utf-8">'
        "<title>Pratinjau Kop</title>"
        f"<style>{style_css(style)}"
        "body{background:#fff;padding:28px 34px;max-width:900px;margin:0 auto;}"
        "</style></head><body>"
        f"{render_letterhead(letterhead, logo_src=logo_src)}"
        '<div class="body"><p>Contoh isi surat ditulis di sini. '
        "Kop di atas akan muncul pada setiap halaman surat.</p></div>"
        "</body></html>"
    )


DEFAULT_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    (
        "Surat Keterangan Aktif Kuliah",
        "mahasiswa",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], dengan ini menerangkan bahwa:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIM</td><td>:</td><td>[NIM]</td></tr>
  <tr><td>Tempat, Tanggal Lahir</td><td>:</td><td>[Tempat_Lahir], [Tanggal_Lahir]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
  <tr><td>Semester</td><td>:</td><td>[Semester]</td></tr>
  <tr><td>Tahun Akademik</td><td>:</td><td>[Tahun_Akademik]</td></tr>
</table>
<p>adalah benar mahasiswa aktif pada [Prodi], [Fakultas], [Lembaga] pada semester [Semester] tahun akademik [Tahun_Akademik].</p>
<p>Surat keterangan ini dibuat untuk keperluan [Keperluan].</p>
<p>Demikian surat keterangan ini dibuat dengan sebenarnya untuk dapat dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Pengantar Penelitian",
        "mahasiswa",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], dengan ini menerangkan bahwa:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIM</td><td>:</td><td>[NIM]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>adalah mahasiswa [Lembaga] yang sedang melaksanakan penelitian dengan judul <strong>&ldquo;[Judul_Penelitian]&rdquo;</strong>.</p>
<p>Sehubungan dengan hal tersebut, kami memohon kesediaan [Tujuan] untuk memberikan izin serta data yang diperlukan dalam rangka penyelesaian penelitian dimaksud.</p>
<p>Demikian surat pengantar ini dibuat untuk dipergunakan sebagaimana mestinya. Atas perhatian dan kerja samanya kami sampaikan terima kasih.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Keterangan Lulus",
        "mahasiswa",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], menerangkan bahwa:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIM</td><td>:</td><td>[NIM]</td></tr>
  <tr><td>Tempat, Tanggal Lahir</td><td>:</td><td>[Tempat_Lahir], [Tanggal_Lahir]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>telah dinyatakan <strong>LULUS</strong> dari [Prodi], [Fakultas], [Lembaga].</p>
<p>Surat keterangan lulus ini dibuat untuk keperluan [Keperluan] dan berlaku sampai dengan diterbitkannya ijazah asli.</p>
<p>Demikian surat keterangan ini dibuat dengan sebenarnya untuk dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Rekomendasi",
        "mahasiswa",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], dengan ini memberikan rekomendasi kepada:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIM</td><td>:</td><td>[NIM]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>Mahasiswa tersebut di atas merupakan mahasiswa yang berkelakuan baik dan aktif dalam kegiatan akademik maupun kemahasiswaan di lingkungan [Lembaga].</p>
<p>Rekomendasi ini diberikan untuk keperluan [Keperluan] sebagaimana diajukan kepada [Tujuan].</p>
<p>Demikian surat rekomendasi ini dibuat untuk dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Keterangan Lainnya",
        "mahasiswa",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], dengan ini menerangkan bahwa:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIM</td><td>:</td><td>[NIM]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>[Keperluan]</p>
<p>Demikian surat keterangan ini dibuat dengan sebenarnya untuk dapat dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Keterangan Aktif Mengajar",
        "dosen",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], dengan ini menerangkan bahwa:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIDN/NIP</td><td>:</td><td>[NIDN]</td></tr>
  <tr><td>Jabatan</td><td>:</td><td>[Jabatan_Dosen]</td></tr>
  <tr><td>Status Kepegawaian</td><td>:</td><td>[Status_Kepegawaian]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>adalah dosen tetap/pada [Prodi], [Fakultas], [Lembaga] dan aktif melaksanakan tugas pendidikan, penelitian, serta pengabdian kepada masyarakat pada tahun akademik [Tahun_Akademik].</p>
<p>Surat keterangan ini dibuat untuk keperluan [Keperluan].</p>
<p>Demikian surat keterangan ini dibuat dengan sebenarnya untuk dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Tugas Dosen",
        "dosen",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], dengan ini menugaskan:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIDN/NIP</td><td>:</td><td>[NIDN]</td></tr>
  <tr><td>Jabatan</td><td>:</td><td>[Jabatan_Dosen]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>untuk melaksanakan tugas [Keperluan] di [Tujuan].</p>
<p>Demikian surat tugas ini dibuat untuk dilaksanakan dengan penuh tanggung jawab dan dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
    (
        "Surat Pengantar Penelitian Dosen",
        "dosen",
        """
<p>Yang bertanda tangan di bawah ini, [Jabatan_Penandatangan] [Lembaga], menerangkan bahwa:</p>
<table class="data">
  <tr><td>Nama</td><td>:</td><td><strong>[Nama]</strong></td></tr>
  <tr><td>NIDN/NIP</td><td>:</td><td>[NIDN]</td></tr>
  <tr><td>Program Studi</td><td>:</td><td>[Prodi]</td></tr>
  <tr><td>Fakultas</td><td>:</td><td>[Fakultas]</td></tr>
</table>
<p>adalah dosen [Lembaga] yang akan melaksanakan penelitian dengan judul <strong>&ldquo;[Judul_Penelitian]&rdquo;</strong>.</p>
<p>Sehubungan dengan hal tersebut, kami memohon kesediaan [Tujuan] untuk memberikan izin dan data yang diperlukan.</p>
<p>Demikian surat pengantar ini dibuat untuk dipergunakan sebagaimana mestinya.</p>
[blok_ttd]
""".strip(),
    ),
)


def seed_defaults(db: Session, settings: Settings) -> None:
    """Isi kop surat dan format surat contoh bila belum ada."""
    if get_active_letterhead(db) is None and db.execute(
        select(LetterHead).limit(1)
    ).scalar_one_or_none() is None:
        db.add(
            LetterHead(
                name="Kop Surat Utama",
                line1="Yayasan Lembaga Pendidikan Islam",
                line2="Sekolah Tinggi Ilmu Tarbiyah",
                line3="( STIT )",
                line4="Buntet Pesantren Cirebon",
                line5=(
                    "Komplek Pondok Buntet Pesantren Desa Mertapadakulon "
                    "Kecamatan Astanajapura Kabupaten Cirebon 45181 "
                    "Telepon 085224108345"
                ),
                accent_color="#0d6b3f",
                is_active=True,
            )
        )

    if db.execute(select(LetterStyle).limit(1)).scalar_one_or_none() is None:
        db.add(default_style())

    existing = {
        row for row in db.execute(select(LetterTemplate.letter_type)).scalars()
    }
    for letter_type, audience, body in DEFAULT_TEMPLATES:
        if letter_type in existing:
            continue
        db.add(
            LetterTemplate(
                letter_type=letter_type,
                name=letter_type,
                body_html=body,
                audience=audience,
                is_active=True,
                created_by="sistem",
            )
        )
    db.commit()
