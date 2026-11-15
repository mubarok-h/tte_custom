from __future__ import annotations

from io import BytesIO

from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature

from .config import Settings
from .pki import tsa_validation_context, validation_context


def _timestamp_info(status) -> dict | None:
    ts = getattr(status, "timestamp_validity", None)
    if ts is None:
        return None
    return {
        "valid": bool(getattr(ts, "valid", False)),
        "trusted": bool(getattr(ts, "trusted", False)),
        "summary": ts.summary() if hasattr(ts, "summary") else None,
    }


def validate_pdf_bytes(data: bytes, settings: Settings) -> dict:
    try:
        reader = PdfFileReader(BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return {
            "valid": False,
            "status": "INVALID",
            "error": f"PDF tidak dapat dibaca: {exc}",
            "signatures": [],
        }

    try:
        vc = validation_context(settings)
        tsa_vc = tsa_validation_context(settings)
    except Exception as exc:  # noqa: BLE001
        return {
            "valid": False,
            "status": "UNKNOWN",
            "error": f"Materi PKI tidak lengkap: {exc}",
            "signatures": [],
        }

    results: list[dict] = []
    for embedded in reader.embedded_signatures:
        try:
            status = validate_pdf_signature(
                embedded,
                vc,
                ts_validation_context=tsa_vc,
                skip_diff=False,
            )
            cert = embedded.signer_cert
            subject = cert.subject.native if cert is not None else {}
            intact = bool(getattr(status, "intact", False))
            trusted = bool(getattr(status, "trusted", False))
            revoked = bool(getattr(status, "revoked", False))
            bottom_line = bool(getattr(status, "bottom_line", False))
            results.append(
                {
                    "field_name": embedded.field_name,
                    "valid": intact and trusted and not revoked and bottom_line,
                    "intact": intact,
                    "trusted": trusted,
                    "revoked": revoked,
                    "bottom_line": bottom_line,
                    "coverage": (
                        status.coverage.name
                        if hasattr(status.coverage, "name")
                        else str(status.coverage)
                    ),
                    "signer": subject,
                    "serial": (
                        str(cert.serial_number) if cert is not None else None
                    ),
                    "signing_time": (
                        status.signer_reported_dt.isoformat()
                        if status.signer_reported_dt
                        else None
                    ),
                    "timestamp": _timestamp_info(status),
                    "summary": status.summary(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "field_name": getattr(embedded, "field_name", None),
                    "valid": False,
                    "error": str(exc),
                }
            )

    valid = bool(results) and all(item.get("valid") for item in results)
    if not results:
        status = "UNSIGNED"
    elif valid:
        status = "VALID"
    elif any(item.get("revoked") for item in results):
        status = "REVOKED"
    else:
        status = "INVALID"

    return {"valid": valid, "status": status, "signatures": results}
