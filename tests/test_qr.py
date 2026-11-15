from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


def test_signed_pdf_contains_verification_link(client, signed_document):
    doc = signed_document
    response = client.get(
        f"/api/v1/documents/{doc['id']}/download",
        params={"t": doc["token"]},
    )
    reader = PdfReader(BytesIO(response.content))
    page = reader.pages[0]
    annotations = page.get("/Annots") or []
    uris = []
    for annotation in annotations:
        obj = annotation.get_object()
        action = obj.get("/A")
        if action and action.get("/URI"):
            uris.append(str(action["/URI"]))
    assert any(doc["short_id"] in uri for uri in uris), uris
    assert any("t=" in uri for uri in uris), uris
