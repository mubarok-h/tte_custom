from __future__ import annotations


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_creates_document(client, uploaded_document):
    doc = uploaded_document
    assert doc["status"] == "uploaded"
    assert len(doc["source_hash"]) == 64
    assert "t=" in doc["verify_url"]


def test_sign_and_verify(client, signed_document):
    doc = signed_document
    assert doc["sign"]["pades_level"] in {"B-B", "B-T", "B-LT"}

    response = client.get(
        f"/api/v1/verify/{doc['short_id']}",
        params={"t": doc["token"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] is True
    assert payload["status"] == "VALID"
    assert len(payload["signatures"]) == 1
    assert payload["signatures"][0]["trusted"] is True


def test_download_requires_token(client, signed_document):
    doc = signed_document
    ok = client.get(
        f"/api/v1/documents/{doc['id']}/download",
        params={"t": doc["token"]},
    )
    assert ok.status_code == 200
    assert ok.content.startswith(b"%PDF")

    bad = client.get(
        f"/api/v1/documents/{doc['id']}/download",
        params={"t": "salah"},
    )
    assert bad.status_code == 403


def test_verify_rejects_bad_token(client, uploaded_document):
    doc = uploaded_document
    response = client.get(
        f"/api/v1/verify/{doc['short_id']}",
        params={"t": "token-palsu"},
    )
    assert response.status_code == 403


def test_portal_renders_valid(client, signed_document):
    doc = signed_document
    response = client.get(
        f"/v/{doc['short_id']}",
        params={"t": doc["token"]},
    )
    assert response.status_code == 200
    assert "VALID" in response.text
    assert "Rektor STIT Buntet Pesantren" in response.text
