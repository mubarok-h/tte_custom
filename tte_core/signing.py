from __future__ import annotations

import logging
from io import BytesIO

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.font import SimpleFontEngineFactory
from pyhanko.pdf_utils.generic import pdf_name, pdf_string
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import (
    AxisAlignment,
    Margins,
    SimpleBoxLayoutRule,
)
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.sign.fields import SigFieldSpec
from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner
from pyhanko.sign.timestamps import HTTPTimeStamper
from pyhanko.stamp import NoOpStampStyle, QRPosition, QRStampStyle

from .config import Settings
from .pki import embedding_validation_context, load_signer

logger = logging.getLogger(__name__)

STAMP_HEADER = "Ditandatangani secara elektronik oleh"
STAMP_FONT = SimpleFontEngineFactory.default_factory()
STAMP_FONT_SIZE = 9
STAMP_LEADING = 10
STAMP_QR_SIZE = 56
STAMP_TEXT_GAP = 6


class SigningError(RuntimeError):
    pass


def _page_info(
    writer: IncrementalPdfFileWriter, page_ix: int = 0
) -> tuple[float, float, object]:
    ref, _ = writer.find_page_for_modification(page_ix)
    page = ref.get_object()
    mediabox = page.get("/MediaBox")
    if mediabox is None:
        return 595.0, 842.0, ref
    x0, y0, x1, y1 = (float(v) for v in mediabox)
    return x1 - x0, y1 - y0, ref


def _add_link_annotation(writer, page_ref, box, url: str) -> None:
    link = generic.DictionaryObject(
        {
            pdf_name("/Type"): pdf_name("/Annot"),
            pdf_name("/Subtype"): pdf_name("/Link"),
            pdf_name("/Rect"): generic.ArrayObject(
                [generic.FloatObject(v) for v in box]
            ),
            pdf_name("/Border"): generic.ArrayObject(
                [generic.FloatObject(0)] * 3
            ),
            pdf_name("/A"): generic.DictionaryObject(
                {
                    pdf_name("/S"): pdf_name("/URI"),
                    pdf_name("/URI"): pdf_string(url),
                }
            ),
        }
    )
    writer.register_annotation(page_ref, writer.add_object(link))


def _signature_box(
    width: float,
    height: float,
    index: int,
    box_w: float,
    box_h: float,
) -> tuple[float, float, float, float]:
    """Susun tanda tangan berurutan: 3 baris per kolom, lalu geser ke kiri."""
    per_column = 3
    column = index // per_column
    row = index % per_column
    x1 = max(80.0, width - 40.0 - column * (box_w + 12.0))
    x0 = max(20.0, x1 - box_w)
    y0 = 40.0 + row * (box_h + 12.0)
    if y0 + box_h > height - 40.0:
        y0 = max(40.0, height - 40.0 - box_h)
    return (x0, y0, x1, y0 + box_h)


def _sign_once(
    input_bytes: bytes,
    *,
    settings: Settings,
    verify_url: str,
    signer_name: str,
    jabatan: str,
    reason: str,
    location: str,
    field_index: int,
    tsa_url: str | None,
    institution: str | None = None,
    faculty: str | None = None,
    program: str | None = None,
    multi_mode: bool = False,
    signer_material=None,
) -> tuple[bytes, dict]:
    signer = signer_material or load_signer(settings)
    writer = IncrementalPdfFileWriter(BytesIO(input_bytes))
    width, height, page_ref = _page_info(writer)

    org = institution or settings.institution
    if multi_mode:
        # Untuk multi-penandatangan, visual hanya menjelaskan bahwa dokumen
        # ditandatangani secara elektronik di institusi, tanpa identitas
        # penanda tangan tertentu.
        stamp_lines = [
            "Ditandatangani secara elektronik",
            f"di {org}",
        ]
    else:
        stamp_lines = [
            STAMP_HEADER,
            "%(signer)s",
            "%(jabatan)s",
            "%(org)s",
        ]
    if faculty:
        stamp_lines.append("%(faculty)s")
    if program:
        stamp_lines.append("%(program)s")

    text_params = {
        "url": verify_url,
        "signer": signer_name,
        "jabatan": jabatan,
        "org": org,
        "faculty": faculty or "",
        "program": program or "",
    }
    rendered_lines = [
        line % text_params if "%(" in line else line for line in stamp_lines
    ]
    text_width = max(len(line) for line in rendered_lines) * (
        STAMP_FONT_SIZE * 0.6
    )
    text_height = len(stamp_lines) * STAMP_LEADING
    qr_size = max(STAMP_QR_SIZE, text_height)
    # Sedikit toleransi agar glyph terakhir tidak tertimpa garis border.
    box_w = qr_size + STAMP_TEXT_GAP + text_width + 4
    box_h = qr_size + 4

    box = _signature_box(width, height, field_index, box_w, box_h)
    # Link hanya pada tanda tangan pertama; anotasi tambahan pada revisi
    # berikutnya dianggap modifikasi mencurigakan oleh diff policy pyHanko.
    if field_index == 0:
        _add_link_annotation(writer, page_ref, box, verify_url)
    field_name = f"Signature{field_index + 1}"
    use_timestamp = bool(tsa_url)
    use_ltv = use_timestamp and settings.embed_ltv

    meta = PdfSignatureMetadata(
        field_name=field_name,
        md_algorithm="sha256",
        reason=reason,
        location=location,
        embed_validation_info=use_ltv,
        use_pades_lta=use_ltv,
        validation_context=(
            embedding_validation_context(settings) if use_ltv else None
        ),
    )
    if field_index == 0:
        style = QRStampStyle(
            stamp_text="\n".join(stamp_lines),
            text_box_style=TextBoxStyle(
                font=STAMP_FONT,
                font_size=STAMP_FONT_SIZE,
                leading=STAMP_LEADING,
                text_color=(0.0, 0.0, 0.0),
                box_layout_rule=SimpleBoxLayoutRule(
                    x_align=AxisAlignment.ALIGN_MID,
                    y_align=AxisAlignment.ALIGN_MID,
                    margins=Margins(
                        left=STAMP_TEXT_GAP, right=0, top=0, bottom=0
                    ),
                ),
            ),
            inner_content_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MID,
                y_align=AxisAlignment.ALIGN_MID,
                margins=Margins.uniform(0),
            ),
            border_width=1,
            border_color=(0.0, 0.0, 0.0),
            qr_position=QRPosition.LEFT_OF_TEXT,
            qr_inner_size=qr_size,
            innsep=0,
        )
    else:
        # Satu dokumen cukup punya satu visual stamp; tanda tangan berikutnya
        # tetap kriptografis tetapi tidak menambah kotak visual.
        style = NoOpStampStyle()
    field_spec = SigFieldSpec(field_name, on_page=0, box=box)
    timestamper = (
        HTTPTimeStamper(tsa_url, timeout=settings.tsa_timeout)
        if use_timestamp
        else None
    )
    pdf_signer = PdfSigner(
        meta,
        signer,
        timestamper=timestamper,
        stamp_style=style,
        new_field_spec=field_spec,
    )
    output = pdf_signer.sign_pdf(
        writer,
        appearance_text_params=text_params,
    )
    data = output.getvalue() if hasattr(output, "getvalue") else bytes(output)
    if use_ltv:
        level = "B-LT"
    elif use_timestamp:
        level = "B-T"
    else:
        level = "B-B"
    return data, {
        "pades_level": level,
        "field_name": field_name,
        "tsa_provider": tsa_url if use_timestamp else None,
    }


def sign_pdf_bytes(
    input_bytes: bytes,
    *,
    settings: Settings,
    verify_url: str,
    signer_name: str,
    jabatan: str,
    reason: str,
    location: str,
    field_index: int = 0,
    institution: str | None = None,
    faculty: str | None = None,
    program: str | None = None,
    multi_mode: bool = False,
    signer_material=None,
) -> tuple[bytes, dict]:
    """Tandatangani PDF, dengan fallback TSA.

    Jika TSA gagal dan tidak wajib, dokumen tetap ditandatangani sebagai
    PAdES B-B (tanpa timestamp). Ini penting untuk operasi offline.
    """
    errors: list[str] = []
    if settings.tsa_enabled:
        for url in settings.tsa_url_list:
            try:
                return _sign_once(
                    input_bytes,
                    settings=settings,
                    verify_url=verify_url,
                    signer_name=signer_name,
                    jabatan=jabatan,
                    reason=reason,
                    location=location,
                    field_index=field_index,
                    tsa_url=url,
                    institution=institution,
                    faculty=faculty,
                    program=program,
                    multi_mode=multi_mode,
                    signer_material=signer_material,
                )
            except Exception as exc:  # noqa: BLE001 - fallback disengaja
                logger.warning("TSA %s gagal: %s", url, exc)
                errors.append(f"{url}: {exc}")

    if settings.tsa_required:
        raise SigningError(
            "Semua TSA gagal dan TTE_TSA_REQUIRED=true: " + "; ".join(errors)
        )

    return _sign_once(
        input_bytes,
        settings=settings,
        verify_url=verify_url,
        signer_name=signer_name,
        jabatan=jabatan,
        reason=reason,
        location=location,
        field_index=field_index,
        tsa_url=None,
        institution=institution,
        faculty=faculty,
        program=program,
        multi_mode=multi_mode,
        signer_material=signer_material,
    )
