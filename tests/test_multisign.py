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


def test_api_can_add_second_signature(client, signed_document):
    doc = signed_document
    created = client.post(
        "/api/v1/sign-requests",
        json={
            "document_id": doc["id"],
            "signer": {
                "name": "Dekan Uji Multi",
                "jabatan": "Dekan",
                "nip": "MULTI-0001",
                "email": "dekan-multi@example.test",
            },
            "reason": "Persetujuan kedua",
        },
    )
    assert created.status_code == 200, created.text
    request_id = created.json()["id"]

    consent = client.post(
        f"/api/v1/sign-requests/{request_id}/consent",
        json={"consent": True},
    )
    assert consent.status_code == 200, consent.text

    signed = client.post(f"/api/v1/sign-requests/{request_id}/sign")
    assert signed.status_code == 200, signed.text
    assert signed.json()["field_name"] == "Signature2"

    verification = client.get(
        f"/api/v1/verify/{doc['short_id']}",
        params={"t": doc["token"]},
    )
    assert verification.status_code == 200, verification.text
    payload = verification.json()
    assert payload["valid"] is True
    assert len(payload["signatures"]) == 2
