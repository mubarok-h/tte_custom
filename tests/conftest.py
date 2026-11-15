from __future__ import annotations

import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="tte-test-"))
PKI_OUT = TEST_ROOT / "pki"

os.environ.update(
    {
        "TTE_DATABASE_URL": f"sqlite:///{TEST_ROOT / 'data' / 'test.db'}",
        "TTE_DATA_DIR": str(TEST_ROOT / "data"),
        "TTE_TSA_ENABLED": "false",
        "TTE_TSA_REQUIRED": "false",
        "TTE_EMBED_LTV": "false",
        "TTE_SIGNING_P12": str(PKI_OUT / "rektor.p12"),
        "TTE_SIGNING_P12_PASSPHRASE": "changeit",
        "TTE_ROOT_CA_PATH": str(PKI_OUT / "root.crt"),
        "TTE_INTERMEDIATE_CA_PATH": str(PKI_OUT / "intermediate.crt"),
        "TTE_CRL_PATH": str(PKI_OUT / "crl" / "stit.crl"),
        "TTE_ROOT_CRL_PATH": str(PKI_OUT / "crl" / "root.crl"),
        "TTE_TSA_ROOTS_DIR": str(PKI_OUT / "tsa"),
        "TTE_VERIFY_BASE_URL": "http://testserver",
        "TTE_REQUIRE_AUTH": "false",
        "TTE_ENABLE_MULTISIGN": "false",
    }
)


@pytest.fixture(scope="session", autouse=True)
def bootstrap_ca() -> None:
    env = os.environ.copy()
    env.update(
        {
            "DEV_DIR": str(TEST_ROOT / "ca"),
            "PKI_OUT": str(PKI_OUT),
            "PKCS12_PASS": "changeit",
        }
    )
    result = subprocess.run(
        ["bash", str(REPO / "ca" / "scripts" / "bootstrap-dev.sh")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Bootstrap CA gagal:\n"
            + result.stdout
            + "\n"
            + result.stderr
        )


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from tte_core.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def sample_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture()
def uploaded_document(client, sample_pdf):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("surat.pdf", sample_pdf, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    doc = response.json()
    doc["token"] = doc["verify_url"].split("t=")[1]
    return doc


@pytest.fixture()
def signed_document(client, uploaded_document):
    doc = uploaded_document
    req = client.post(
        "/api/v1/sign-requests",
        json={
            "document_id": doc["id"],
            "signer": {
                "name": "Rektor STIT Buntet Pesantren",
                "jabatan": "Rektor",
                "nip": "1987654321",
                "email": "rektor@stit-buntetpesantren.ac.id",
            },
            "reason": "Pengesahan Dokumen Resmi",
        },
    )
    assert req.status_code == 200, req.text
    request_id = req.json()["id"]
    consent = client.post(
        f"/api/v1/sign-requests/{request_id}/consent",
        json={"consent": True},
    )
    assert consent.status_code == 200, consent.text
    sign = client.post(f"/api/v1/sign-requests/{request_id}/sign")
    assert sign.status_code == 200, sign.text
    doc["sign"] = sign.json()
    return doc
