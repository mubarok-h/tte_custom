from __future__ import annotations

import logging
from io import BytesIO

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.generic import pdf_name, pdf_string
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign.fields import SigFieldSpec
from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner
from pyhanko.sign.timestamps import HTTPTimeStamper
from pyhanko.stamp import QRPosition, QRStampStyle

from .config import Settings
from .pki import embedding_validation_context, load_signer

logger = logging.getLogger(__name__)

STAMP_TEXT = (
    "Ditandatangani secara elektronik oleh:\n"
    "%(signer)s\n"
    "%(jabatan)s\n"
    "%(ts)s\n"
    "Verifikasi: %(url)s"
)


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


def _signature_box(width: float, height: float, index: int) -> tuple[float, float, float, float]:
    box_w, box_h = 270.0, 125.0
    x1 = max(80.0, width - 40.0)
    x0 = max(20.0, x1 - box_w)
    y0 = 40.0 + (index % 3) * (box_h + 12.0)
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
) -> tuple[bytes, dict]:
    signer = load_signer(settings)
    writer = IncrementalPdfFileWriter(BytesIO(input_bytes))
    width, height, page_ref = _page_info(writer)
    box = _signature_box(width, height, field_index)
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
    style = QRStampStyle(
        stamp_text=STAMP_TEXT,
        qr_position=QRPosition.LEFT_OF_TEXT,
        qr_inner_size=74,
    )
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
        appearance_text_params={
            "url": verify_url,
            "signer": signer_name,
            "jabatan": jabatan,
        },
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
    )
