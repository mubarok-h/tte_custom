from __future__ import annotations


def test_tampered_pdf_is_invalid(client, signed_document):
    doc = signed_document
    response = client.get(
        f"/api/v1/documents/{doc['id']}/download",
        params={"t": doc["token"]},
    )
    data = bytearray(response.content)
    marker = data.find(b"stream")
    assert marker > 0
    data[marker + 10] ^= 0xFF

    result = client.post(
        "/api/v1/verify",
        files={"file": ("tampered.pdf", bytes(data), "application/pdf")},
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["valid"] is False
    assert payload["status"] == "INVALID"


def test_valid_pdf_upload_verifies(client, signed_document):
    doc = signed_document
    response = client.get(
        f"/api/v1/documents/{doc['id']}/download",
        params={"t": doc["token"]},
    )
    result = client.post(
        "/api/v1/verify",
        files={"file": ("signed.pdf", response.content, "application/pdf")},
    )
    assert result.status_code == 200
    assert result.json()["valid"] is True


def test_unsigned_pdf_is_reported(client, sample_pdf):
    result = client.post(
        "/api/v1/verify",
        files={"file": ("plain.pdf", sample_pdf, "application/pdf")},
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["valid"] is False
    assert payload["status"] == "UNSIGNED"
