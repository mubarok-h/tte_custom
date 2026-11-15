from __future__ import annotations

from pathlib import Path

from tte_core.config import get_settings
from tte_core.signing import sign_pdf_bytes
from tte_core.verification import validate_pdf_bytes


def test_two_signatures_incremental(sample_pdf):
    settings = get_settings()
    first, info1 = sign_pdf_bytes(
        sample_pdf,
        settings=settings,
        verify_url="https://verif.example/v/ABC?t=1",
        signer_name="Rektor",
        jabatan="Rektor",
        reason="Pengesahan",
        location="Buntet",
        field_index=0,
    )
    assert info1["field_name"] == "Signature1"

    second, info2 = sign_pdf_bytes(
        first,
        settings=settings,
        verify_url="https://verif.example/v/ABC?t=1",
        signer_name="Dekan",
        jabatan="Dekan",
        reason="Persetujuan",
        location="Buntet",
        field_index=1,
    )
    assert info2["field_name"] == "Signature2"

    result = validate_pdf_bytes(second, settings)
    assert result["valid"] is True
    assert len(result["signatures"]) == 2
